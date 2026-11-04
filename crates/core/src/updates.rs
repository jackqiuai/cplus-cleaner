use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
pub const RELEASES: &str = "https://github.com/jackqiuai/cplus-cleaner/releases";
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Release {
    pub tag: String,
    pub name: String,
    pub url: String,
    pub newer: bool,
    pub notes: String,
}
pub fn check() -> Result<Release> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(18))
        .user_agent(format!("CPlusCleaner/{}", crate::VERSION))
        .build()?;
    let response = client
        .get("https://api.github.com/repos/jackqiuai/cplus-cleaner/releases/latest")
        .send()?;
    if !response.status().is_success() {
        bail!(
            "GitHub update service returned HTTP {}",
            response.status().as_u16()
        );
    }
    let v: serde_json::Value = response.json()?;
    if v["draft"] == true || v["prerelease"] == true {
        bail!("No stable release is available");
    }
    let tag = v["tag_name"].as_str().context("Missing release version")?;
    let url = v["html_url"].as_str().context("Missing release address")?;
    if !url.starts_with("https://github.com/jackqiuai/cplus-cleaner/releases/tag/") {
        bail!("Unexpected release address");
    }
    let published = semver::Version::parse(tag.trim_start_matches('v'))?;
    let current = semver::Version::parse(crate::VERSION)?;
    Ok(Release {
        tag: tag.into(),
        name: v["name"].as_str().unwrap_or(tag).into(),
        url: url.into(),
        newer: published > current,
        notes: v["body"].as_str().unwrap_or("").into(),
    })
}
