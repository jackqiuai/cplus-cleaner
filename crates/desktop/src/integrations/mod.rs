use anyhow::Result;
use tray_icon::{TrayIcon, TrayIconBuilder};
pub struct Tray {
    pub _icon: TrayIcon,
}
impl Tray {
    pub fn new() -> Result<Self> {
        let image = image::load_from_memory(
            cplus_core::resource("assets/app-icon.png").unwrap_or_default(),
        )?
        .into_rgba8();
        let (w, h) = image.dimensions();
        let icon = tray_icon::Icon::from_rgba(image.into_raw(), w, h)?;
        let tray = TrayIconBuilder::new()
            .with_menu_on_left_click(false)
            .with_tooltip("CPlus Cleaner")
            .with_icon(icon)
            .build()?;
        Ok(Self { _icon: tray })
    }
}
