// Ed25519 identities and TLS peer verification.
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use crate::Draft;
use anyhow::{bail, Context, Result};
use rcgen::{CertificateParams, KeyPair, PKCS_ED25519};
use rustls::crypto::{verify_tls13_signature, CryptoProvider};
use rustls::pki_types::{CertificateDer, PrivatePkcs8KeyDer, UnixTime};
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
    pub fn server_config(&self, drafts: &[Draft]) -> Result<noq::ServerConfig> {
        if drafts.is_empty() {
            bail!("at least one draft is required");
        }
        let provider = Arc::new(rustls::crypto::aws_lc_rs::default_provider());
        let cert =
            CertificateParams::new(vec!["common-golf.local".into()])?.self_signed(&self.key)?;
        let mut tls = rustls::ServerConfig::builder_with_provider(provider.clone())
            .with_protocol_versions(&[&rustls::version::TLS13])?
            .with_client_cert_verifier(Arc::new(PeerVerifier(provider)))
            .with_single_cert(
                vec![cert.der().clone()],
                PrivatePkcs8KeyDer::from(self.key.serialize_der()).into(),
            )?;
        tls.alpn_protocols = drafts.iter().map(|d| d.alpn().to_vec()).collect();
        let mut config = noq::ServerConfig::with_crypto(Arc::new(
            noq::crypto::rustls::QuicServerConfig::try_from(tls)?,
        ));
        config.transport_config(transport()?);
        Ok(config)
    }
    pub fn client_config(&self, drafts: &[Draft]) -> Result<noq::ClientConfig> {
        if drafts.is_empty() {
            bail!("at least one draft is required");
        }
        let provider = Arc::new(rustls::crypto::aws_lc_rs::default_provider());
        let cert =
            CertificateParams::new(vec!["common-golf.local".into()])?.self_signed(&self.key)?;
        let mut tls = rustls::ClientConfig::builder_with_provider(provider.clone())
            .with_protocol_versions(&[&rustls::version::TLS13])?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(PeerVerifier(provider)))
            .with_client_auth_cert(
                vec![cert.der().clone()],
                PrivatePkcs8KeyDer::from(self.key.serialize_der()).into(),
            )?;
        tls.alpn_protocols = drafts.iter().map(|d| d.alpn().to_vec()).collect();
        let mut config = noq::ClientConfig::new(Arc::new(
            noq::crypto::rustls::QuicClientConfig::try_from(tls)?,
        ));
        config.transport_config(transport()?);
        Ok(config)
    }
}
fn transport() -> Result<Arc<noq::TransportConfig>> {
    let mut config = noq::TransportConfig::default();
    config.max_idle_timeout(Some(Duration::from_secs(90).try_into()?));
    config.keep_alive_interval(Some(Duration::from_secs(15)));
    config.max_concurrent_uni_streams(32_u32.into());
    config.max_concurrent_bidi_streams(32_u32.into());
    config.datagram_receive_buffer_size(None);
    Ok(Arc::new(config))
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
            ed25519_public_key(cert)
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

#[derive(Debug)]
struct PeerVerifier(Arc<CryptoProvider>);

impl ClientCertVerifier for PeerVerifier {
    fn offer_client_auth(&self) -> bool {
        true
    }

    fn client_auth_mandatory(&self) -> bool {
        false
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
        ed25519_public_key(end_entity)?;
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
    fn verify_server_cert(
        &self,
        cert: &CertificateDer<'_>,
        _: &[CertificateDer<'_>],
        _: &rustls::pki_types::ServerName<'_>,
        _: &[u8],
        _: UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        ed25519_public_key(cert)?;
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
