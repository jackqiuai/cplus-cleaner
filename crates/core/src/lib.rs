//! Feature engines and operation models, independent of the desktop UI.
pub mod common;
pub mod features;
pub mod jobs;
pub mod models;
pub mod storage;
pub mod updates;
pub mod validation;

pub use models::*;
pub use storage::{Settings, Store};
pub static RESOURCES: include_dir::Dir<'static> =
    include_dir::include_dir!("$CARGO_MANIFEST_DIR/../../resources");
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub fn resource(path: &str) -> Option<&'static [u8]> {
    RESOURCES.get_file(path).map(|f| f.contents())
}
pub fn resource_json(path: &str) -> anyhow::Result<serde_json::Value> {
    Ok(serde_json::from_slice(resource(path).ok_or_else(
        || anyhow::anyhow!("Missing resource: {path}"),
    )?)?)
}
