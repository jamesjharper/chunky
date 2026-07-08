//! Private SQLite and cache-entry constants.

pub const SCHEMA_VERSION: i64 = 1;
pub const ENTRY_COMMITTED: &str = "committed";
pub const ENTRY_INVALID: &str = "invalid";
pub const ENTRY_DELETING: &str = "deleting";

pub fn is_safe_extension(extension: &str) -> bool {
    let bytes = extension.as_bytes();
    if bytes.len() < 2 || bytes.len() > 33 || bytes[0] != b'.' {
        return false;
    }
    bytes[1..]
        .iter()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(*byte, b'.' | b'_' | b'-'))
        && bytes[1].is_ascii_alphanumeric()
}
