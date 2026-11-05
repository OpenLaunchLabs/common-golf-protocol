// Ed25519 identities and TLS peer verification.
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use crate::Draft;
use anyhow::{bail, Context, Result};
use rcgen::{CertificateParams, KeyPair, PKCS_ED25519};
use rustls::crypto::{verify_tls13_signature, verify_tls13_signature_with_raw_key, CryptoProvider};
use rustls::pki_types::{CertificateDer, PrivatePkcs8KeyDer, SubjectPublicKeyInfoDer, UnixTime};
use rustls::server::danger::{ClientCertVerified, ClientCertVerifier};
use rustls::{DigitallySignedStruct, DistinguishedName, SignatureScheme};
use x509_parser::prelude::FromDer;

/// Persistent Ed25519 identity. Certificates may be renewed without changing its fingerprint.
pub struct Identity {
    key: KeyPair,
}
impl Identity {
    pub fn load_or_create(directory: &Path) -> Result<Self> {
        std::fs::create_dir_all(directory)?;
        let path = directory.join("identity.pem");
        let key = match std::fs::read_to_string(&path) {
            Ok(pem) => KeyPair::from_pem(&pem).context("parse Ed25519 identity")?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let key = KeyPair::generate_for(&PKCS_ED25519)?;
                use std::io::Write;
                let mut options = std::fs::OpenOptions::new();
                options.write(true).create_new(true);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::OpenOptionsExt;
                    options.mode(0o600);
                }
                options
                    .open(&path)?
                    .write_all(key.serialize_pem().as_bytes())?;
                key
            }
            Err(error) => return Err(error.into()),
        };
        if key.algorithm() != &PKCS_ED25519 {
            bail!("identity must be Ed25519");
        }
        Ok(Self { key })
    }
    pub fn generate() -> Result<Self> {
        Ok(Self {
            key: KeyPair::generate_for(&PKCS_ED25519)?,
        })
    }
    pub fn fingerprint(&self) -> String {
        hex(self.key.public_key_raw())
    }
    fn raw_profile(drafts: &[Draft]) -> Result<bool> {
        if drafts.is_empty() {
            bail!("at least one draft is required");
        }
        let raw = drafts.contains(&Draft::Three);
        if raw && drafts.iter().any(|d| *d != Draft::Three) {
            bail!("raw-key draft 3 and legacy X.509 drafts need separate TLS configurations");
        }
        Ok(raw)
    }
    fn certified_raw_key(
        &self,
        provider: &CryptoProvider,
    ) -> Result<Arc<rustls::sign::CertifiedKey>> {
        let signing = provider
            .key_provider
            .load_private_key(PrivatePkcs8KeyDer::from(self.key.serialize_der()).into())?;
        let spki = signing
            .public_key()
            .context("missing Ed25519 public key")?
            .as_ref()
            .to_vec();
        Ok(Arc::new(rustls::sign::CertifiedKey::new(
            vec![CertificateDer::from(spki)],
            signing,
        )))
    }
    pub fn server_config(&self, drafts: &[Draft]) -> Result<noq::ServerConfig> {
        let raw = Self::raw_profile(drafts)?;
        let provider = Arc::new(rustls::crypto::aws_lc_rs::default_provider());
        let builder = rustls::ServerConfig::builder_with_provider(provider.clone())
            .with_protocol_versions(&[&rustls::version::TLS13])?
            .with_client_cert_verifier(Arc::new(PeerVerifier(provider.clone(), raw)));
        let mut tls = if raw {
            builder.with_cert_resolver(Arc::new(
                rustls::server::AlwaysResolvesServerRawPublicKeys::new(
                    self.certified_raw_key(&provider)?,
                ),
            ))
        } else {
            let cert =
                CertificateParams::new(vec!["common-golf.local".into()])?.self_signed(&self.key)?;
            builder.with_single_cert(
                vec![cert.der().clone()],
                PrivatePkcs8KeyDer::from(self.key.serialize_der()).into(),
            )?
        };
        tls.alpn_protocols = drafts.iter().map(|d| d.alpn().to_vec()).collect();
        let mut config = noq::ServerConfig::with_crypto(Arc::new(
            noq::crypto::rustls::QuicServerConfig::try_from(tls)?,
        ));
        config.transport_config(transport(raw)?);
        Ok(config)
    }
    pub fn client_config(&self, drafts: &[Draft]) -> Result<noq::ClientConfig> {
        let raw = Self::raw_profile(drafts)?;
        let provider = Arc::new(rustls::crypto::aws_lc_rs::default_provider());
        let builder = rustls::ClientConfig::builder_with_provider(provider.clone())
            .with_protocol_versions(&[&rustls::version::TLS13])?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(PeerVerifier(provider.clone(), raw)));
        let mut tls = if raw {
            builder.with_client_cert_resolver(Arc::new(
                rustls::client::AlwaysResolvesClientRawPublicKeys::new(
                    self.certified_raw_key(&provider)?,
                ),
            ))
        } else {
            let cert =
                CertificateParams::new(vec!["common-golf.local".into()])?.self_signed(&self.key)?;
            builder.with_client_auth_cert(
                vec![cert.der().clone()],
                PrivatePkcs8KeyDer::from(self.key.serialize_der()).into(),
            )?
        };
        tls.alpn_protocols = drafts.iter().map(|d| d.alpn().to_vec()).collect();
        if raw {
            tls.enable_sni = false;
        }
        let mut config = noq::ClientConfig::new(Arc::new(
            noq::crypto::rustls::QuicClientConfig::try_from(tls)?,
        ));
        config.transport_config(transport(raw)?);
        Ok(config)
    }
}
fn transport(raw: bool) -> Result<Arc<noq::TransportConfig>> {
    let mut config = noq::TransportConfig::default();
    config.max_idle_timeout(Some(
        Duration::from_secs(if raw { 30 } else { 90 }).try_into()?,
    ));
    config.keep_alive_interval(Some(Duration::from_secs(if raw { 5 } else { 15 })));
    config.max_concurrent_uni_streams(32_u32.into());
    config.max_concurrent_bidi_streams(32_u32.into());
    config.datagram_receive_buffer_size(if raw { Some(262144) } else { None });
    config.datagram_send_buffer_size(262144);
    Ok(Arc::new(config))
}
pub fn valid_fingerprint(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn check_expected_key(connection: &noq::Connection, expected: &str) -> Result<()> {
    if !valid_fingerprint(expected) || peer_fingerprint(connection)?.as_deref() != Some(expected) {
        connection.close(1u32.into(), b"discovery key mismatch");
        bail!("missing, malformed, or mismatched discovery key");
    }
    Ok(())
}
pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
pub fn peer_fingerprint(connection: &noq::Connection) -> Result<Option<String>> {
    let Some(identity) = connection.peer_identity() else {
        return Ok(None);
    };
    let certificates = identity
        .downcast::<Vec<CertificateDer<'static>>>()
        .map_err(|_| anyhow::anyhow!("unsupported peer identity"))?;
    certificates
        .first()
        .map(|cert| {
            if raw_ed25519_public_key(cert).is_ok() {
                raw_ed25519_public_key(cert)
            } else {
                ed25519_public_key(cert)
            }
            .map(|bytes| hex(&bytes))
            .map_err(Into::into)
        })
        .transpose()
}
pub fn ed25519_public_key(cert: &CertificateDer<'_>) -> Result<[u8; 32], rustls::Error> {
    let (remaining, parsed) = x509_parser::certificate::X509Certificate::from_der(cert.as_ref())
        .map_err(|_| rustls::Error::InvalidCertificate(rustls::CertificateError::BadEncoding))?;
    let public = parsed.public_key();
    if !remaining.is_empty()
        || public.algorithm.algorithm.to_id_string() != "1.3.101.112"
        || public.algorithm.parameters.is_some()
        || public.subject_public_key.unused_bits != 0
    {
        return Err(rustls::Error::InvalidCertificate(
            rustls::CertificateError::BadEncoding,
        ));
    }
    public
        .subject_public_key
        .data
        .as_ref()
        .try_into()
        .map_err(|_| rustls::Error::InvalidCertificate(rustls::CertificateError::BadEncoding))
}

pub fn raw_ed25519_public_key(cert: &CertificateDer<'_>) -> Result<[u8; 32], rustls::Error> {
    // RFC8410 Ed25519 SubjectPublicKeyInfo: algorithm has no parameters.
    const PREFIX: &[u8] = &[
        0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00,
    ];
    if cert.len() == 44 && cert.as_ref().starts_with(PREFIX) {
        Ok(cert.as_ref()[12..].try_into().unwrap())
    } else {
        Err(rustls::Error::InvalidCertificate(
            rustls::CertificateError::BadEncoding,
        ))
    }
}
#[derive(Debug)]
struct PeerVerifier(Arc<CryptoProvider>, bool);

impl ClientCertVerifier for PeerVerifier {
    fn offer_client_auth(&self) -> bool {
        true
    }

    fn client_auth_mandatory(&self) -> bool {
        self.1
    }
    fn requires_raw_public_keys(&self) -> bool {
        self.1
    }

    fn root_hint_subjects(&self) -> &[DistinguishedName] {
        &[]
    }

    fn verify_client_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _now: UnixTime,
    ) -> Result<ClientCertVerified, rustls::Error> {
        if self.1 {
            raw_ed25519_public_key(end_entity)?;
        } else {
            ed25519_public_key(end_entity)?;
        }
        Ok(ClientCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &CertificateDer<'_>,
        _dss: &DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Err(rustls::Error::PeerIncompatible(
            rustls::PeerIncompatible::Tls12NotOffered,
        ))
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        if dss.scheme != SignatureScheme::ED25519 {
            return Err(rustls::Error::InvalidCertificate(
                rustls::CertificateError::BadSignature,
            ));
        }
        if self.1 {
            return verify_tls13_signature_with_raw_key(
                message,
                &SubjectPublicKeyInfoDer::from(cert.as_ref()),
                dss,
                &self.0.signature_verification_algorithms,
            );
        }
        verify_tls13_signature(
            message,
            cert,
            dss,
            &self.0.signature_verification_algorithms,
        )
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        vec![SignatureScheme::ED25519]
    }
}

impl rustls::client::danger::ServerCertVerifier for PeerVerifier {
    fn requires_raw_public_keys(&self) -> bool {
        self.1
    }
    fn verify_server_cert(
        &self,
        cert: &CertificateDer<'_>,
        _: &[CertificateDer<'_>],
        _: &rustls::pki_types::ServerName<'_>,
        _: &[u8],
        _: UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        if self.1 {
            raw_ed25519_public_key(cert)?;
        } else {
            ed25519_public_key(cert)?;
        }
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        ClientCertVerifier::verify_tls12_signature(self, message, cert, dss)
    }
    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        ClientCertVerifier::verify_tls13_signature(self, message, cert, dss)
    }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        vec![SignatureScheme::ED25519]
    }
}
