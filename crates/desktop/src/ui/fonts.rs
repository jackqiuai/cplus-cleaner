use iced::{Font, font};
use std::{path::PathBuf, sync::OnceLock};

fn directory() -> PathBuf {
    PathBuf::from(std::env::var_os("WINDIR").unwrap_or_else(|| "C:\\Windows".into())).join("Fonts")
}

/// Use the Windows-installed family, including its real regular and bold faces.
pub fn regular() -> Font {
    static FONT: OnceLock<Font> = OnceLock::new();
    *FONT.get_or_init(|| {
        Font::with_name(if directory().join("msyh.ttc").is_file() {
            "Microsoft YaHei"
        } else {
            "Segoe UI"
        })
    })
}

pub fn bold() -> Font {
    Font {
        weight: font::Weight::Bold,
        ..regular()
    }
}

/// Read the installed font files at startup; no font artwork is bundled.
pub fn system_faces() -> impl Iterator<Item = Vec<u8>> {
    ["msyh.ttc", "msyhbd.ttc"]
        .into_iter()
        .filter_map(|name| std::fs::read(directory().join(name)).ok())
}
