// Common Golf Protocol message framing.
use anyhow::{bail, Result};
use serde_json::Value;
use tokio::io::{AsyncRead, AsyncReadExt};

pub const MAX_MESSAGE_BYTES: usize = 65_535;
pub const NOT_INTERESTED: u32 = 1;
pub const MALFORMED: u32 = 2;

pub fn encode(value: &Value) -> Result<Vec<u8>> {
    if !value.is_object() {
        bail!("Common Golf messages must be JSON objects");
    }
    let body = serde_json::to_vec(value)?;
    if body.len() > MAX_MESSAGE_BYTES {
        bail!("Common Golf message exceeds 65535 bytes");
    }
    let mut frame = Vec::with_capacity(4 + body.len());
    frame.extend_from_slice(&(body.len() as u32).to_be_bytes());
    frame.extend_from_slice(&body);
    Ok(frame)
}

pub async fn read_prefix<R: AsyncRead + Unpin>(reader: &mut R) -> Result<Option<u32>> {
    let mut prefix = [0; 4];
    let first = reader.read(&mut prefix[..1]).await?;
    if first == 0 {
        return Ok(None);
    }
    reader.read_exact(&mut prefix[1..]).await?;
    Ok(Some(u32::from_be_bytes(prefix)))
}

pub async fn read_message<R: AsyncRead + Unpin>(reader: &mut R) -> Result<Option<Value>> {
    let Some(length) = read_prefix(reader).await? else {
        return Ok(None);
    };
    if length as usize > MAX_MESSAGE_BYTES {
        bail!("Common Golf frame exceeds 65535 bytes");
    }
    let mut payload = vec![0; length as usize];
    reader.read_exact(&mut payload).await?;
    let value: Value = serde_json::from_slice(&payload)?;
    if !value.is_object() {
        bail!("Common Golf payload is not a JSON object");
    }
    Ok(Some(value))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncWriteExt;

    #[tokio::test]
    async fn split_prefixes_and_coalesced_messages() {
        let (mut writer, mut reader) = tokio::io::duplex(32);
        let message = serde_json::json!({"type":"info", "protocol_version":"0.1.0"});
        let expected = message.clone();
        let producer = tokio::spawn(async move {
            for byte in 0_u32.to_be_bytes() {
                writer.write_all(&[byte]).await.unwrap();
            }
            let mut bytes = encode(&message).unwrap();
            bytes.extend(encode(&message).unwrap());
            for chunk in bytes.chunks(3) {
                writer.write_all(chunk).await.unwrap();
            }
        });
        assert_eq!(read_prefix(&mut reader).await.unwrap(), Some(0));
        assert_eq!(
            read_message(&mut reader).await.unwrap(),
            Some(expected.clone())
        );
        assert_eq!(read_message(&mut reader).await.unwrap(), Some(expected));
        assert_eq!(read_message(&mut reader).await.unwrap(), None);
        producer.await.unwrap();
    }

    #[tokio::test]
    async fn frame_size_boundary_and_invalid_payloads() {
        let body = serde_json::json!({"x":"x".repeat(MAX_MESSAGE_BYTES - 8)});
        let bytes = encode(&body).unwrap();
        assert_eq!(bytes.len(), MAX_MESSAGE_BYTES + 4);
        assert_eq!(
            read_message(&mut bytes.as_slice()).await.unwrap(),
            Some(body)
        );
        for bytes in [
            (MAX_MESSAGE_BYTES as u32 + 1).to_be_bytes().to_vec(),
            vec![0, 0, 0, 2, b'[', b']'],
            vec![0, 0, 0, 2, b'{'],
            vec![0, 0],
            vec![0, 0, 0, 2, 0xff, 0xff],
        ] {
            assert!(read_message(&mut bytes.as_slice()).await.is_err());
        }
    }
}
