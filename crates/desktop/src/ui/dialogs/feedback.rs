use super::*;
use cplus_platform_windows::registry::{HKEY_LOCAL_MACHINE, KEY_WOW64_64KEY, Key};
use iced::{
    Color,
    widget::{button, column, responsive, stack, svg},
};
use std::sync::OnceLock;

const PROJECT: &str = "https://github.com/jackqiuai/cplus-cleaner";
const BUG: &str = "<rect x='6' y='7' width='12' height='14' rx='6'/><path d='M9 7V5a3 3 0 0 1 6 0v2M9 2l1 2m5-2-1 2M3 9l3 2m-4 4h4m-3 6 4-3m14-9-3 2m4 4h-4m3 6-4-3M7 11h10'/>";
const BULB: &str = "<path d='M8 15a6 6 0 1 1 8 0c-1 1-1 2-1 3H9c0-1 0-2-1-3ZM9 21h6M12 1v2M3 4l2 2M1 11h2m18 0h2m-4-5 2-2M3 18l2-2m14 0 2 2'/>";
const DOCUMENT: &str = "<path d='M5 2h10l4 4v16H5ZM15 2v5h4M8 11h8m-8 4h8m-8 4h5'/>";
const COPY: &str = "<rect x='8' y='7' width='12' height='15' rx='1'/><path d='M16 7V2H3v16h5'/>";
const LINK: &str = "<path d='m10 13 4-4m-7 6-2 2a4 4 0 0 0 6 6l4-4a4 4 0 0 0 0-6m2-4 2-2a4 4 0 0 0-6-6L9 5a4 4 0 0 0 0 6' transform='translate(0 -1)'/>";
const GITHUB: &str = "<path fill='#19313b' stroke='none' d='M12 1a11 11 0 0 0-3.48 21.44c.55.1.75-.24.75-.53v-2.05c-3.06.66-3.71-1.3-3.71-1.3-.5-1.27-1.22-1.61-1.22-1.61-1-.68.08-.67.08-.67 1.1.08 1.68 1.13 1.68 1.13.98 1.68 2.58 1.2 3.21.92.1-.71.38-1.2.7-1.47-2.44-.28-5-1.22-5-5.44 0-1.2.43-2.18 1.13-2.95-.11-.28-.49-1.4.11-2.91 0 0 .92-.3 3.03 1.13a10.55 10.55 0 0 1 5.5 0c2.1-1.43 3.02-1.13 3.02-1.13.6 1.51.22 2.63.11 2.91.71.77 1.13 1.75 1.13 2.95 0 4.23-2.57 5.16-5.01 5.43.4.34.75 1.01.75 2.04v3.02c0 .3.2.64.76.53A11 11 0 0 0 12 1Z'/>";

fn icon(path: &str, color: &str, size: u32) -> Element<'static, Message> {
    svg(svg::Handle::from_memory(format!(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{color}' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'>{path}</svg>"
    ).into_bytes())).width(size).height(size).into()
}

fn badge(content: Element<'static, Message>, color: Color, size: u32) -> Element<'static, Message> {
    container(content)
        .center(size)
        .style(move |_| container::Style {
            background: Some(color.into()),
            border: iced::Border {
                radius: (size as f32 / 2.0).into(),
                ..Default::default()
            },
            ..Default::default()
        })
        .into()
}

pub(super) fn view(app: &App, id: window::Id) -> Element<'_, Message> {
    let hero = container(responsive(move |size| {
    let hero_icon = svg(svg::Handle::from_memory(br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><linearGradient id="mint" x2="1" y2="1"><stop stop-color="#13dca0"/><stop offset="1" stop-color="#0abb83"/></linearGradient></defs><circle cx="50" cy="50" r="50" fill="#eafbf5"/><rect x="17" y="17" width="66" height="66" rx="25" fill="url(#mint)"/><path d="M30 48c0-13 9-22 22-22s22 9 22 22-10 22-23 22h-8l-12 7 3-14c-3-4-4-9-4-15Z" fill="white"/><g fill="#16c990"><circle cx="42" cy="49" r="3.5"/><circle cx="53" cy="49" r="3.5"/><circle cx="64" cy="49" r="3.5"/></g></svg>"##))
        .width(98).height(98);
    let waves = String::from_utf8_lossy(cplus_core::resource("assets/ui/home-hero-waves.svg").unwrap_or_default())
        .replacen("<svg ", &format!("<svg width='{}' height='108' ", size.width.round().max(1.0)), 1);
    stack![
        svg(svg::Handle::from_memory(waves.into_bytes()))
        .width(Length::Fill)
        .height(108),
        container(
            row![
                hero_icon,
                column![
                    bold(app.t("Feedback"), 27),
                    small(app.t("Help improve CPlus Cleaner")).size(16)
                ]
                .spacing(6)
            ]
            .spacing(22)
            .align_y(iced::Alignment::Center)
        )
        .padding([4, 12])
    ]
    .height(108).into()
    })).height(108);
    let option = |problem: bool| {
        let (title, description, button_text, glyph, color, background, url) = if problem {
            (
                "Report a problem",
                "Having trouble? Describe the issue and steps to reproduce.",
                "Report on GitHub",
                BUG,
                "#ff5966",
                Color::from_rgb8(0xff, 0xee, 0xf0),
                format!("{PROJECT}/issues/new/choose"),
            )
        } else {
            (
                "Feature suggestions",
                "Share your suggestions and new ideas.",
                "Submit a suggestion",
                BULB,
                "#13bd86",
                theme::PALE,
                format!("{PROJECT}/issues/new?title=%5BFeature%20request%5D%20"),
            )
        };
        container(
            column![
                row![
                    badge(icon(glyph, color, 32), background, 60),
                    bold(app.t(title), 20)
                ]
                .spacing(16)
                .align_y(iced::Alignment::Center),
                small(app.t(description)).size(14).height(42),
                button(
                    bold(app.t(button_text), 16)
                        .color(if problem { Color::WHITE } else { theme::MINT })
                        .width(Length::Fill)
                        .align_x(iced::Alignment::Center)
                )
                .width(Length::Fill)
                .padding([11, 8])
                .style(if problem {
                    theme::primary
                } else {
                    theme::subtle
                })
                .on_press(Message::Open(url))
            ]
            .spacing(12),
        )
        .padding(18)
        .width(Length::FillPortion(1))
        .style(theme::card)
    };
    let mut details = Column::new().spacing(8);
    for (name, value) in environment_rows(app) {
        details =
            details.push(row![small(name).size(14).width(100), small(value).size(14)].spacing(8));
    }
    let environment = container(
        row![
            column![
                row![
                    container(icon(DOCUMENT, "#13bd86", 25))
                        .padding(10)
                        .style(|_| container::Style {
                            background: Some(theme::PALE.into()),
                            border: iced::Border {
                                radius: 10.0.into(),
                                ..Default::default()
                            },
                            ..Default::default()
                        }),
                    bold(app.t("Environment information"), 18)
                ]
                .spacing(16)
                .align_y(iced::Alignment::Center),
                container(details).padding(iced::Padding {
                    left: 61.0,
                    ..Default::default()
                })
            ]
            .spacing(8)
            .width(Length::Fill),
            button(
                row![
                    icon(COPY, "#13bd86", 19),
                    bold(app.t("Copy environment information"), 14).color(theme::MINT)
                ]
                .spacing(9)
                .align_y(iced::Alignment::Center)
            )
            .padding([11, 16])
            .style(theme::subtle)
            .on_press(Message::CopyEnvironment)
        ]
        .spacing(16)
        .align_y(iced::Alignment::Center),
    )
    .padding(18)
    .width(Length::Fill)
    .style(theme::hero);
    let footer = row![
        column![
            row![
                icon(LINK, "#7a8fa3", 17),
                small(app.t("Opens the project's GitHub Issues page in your browser."))
            ]
            .spacing(8)
            .align_y(iced::Alignment::Center),
            button(
                row![
                    icon(GITHUB, "#19313b", 22),
                    label("github.com/jackqiuai/cplus-cleaner")
                        .color(Color::from_rgb8(0x16, 0x77, 0xff))
                ]
                .spacing(10)
                .align_y(iced::Alignment::Center)
            )
            .padding([3, 0])
            .style(theme::link)
            .on_press(Message::Open(PROJECT.into()))
        ]
        .spacing(12)
        .width(Length::Fill),
        button(
            bold(app.t("Close"), 15)
                .color(theme::MINT)
                .width(Length::Fill)
                .align_x(iced::Alignment::Center)
        )
        .width(132)
        .padding([12, 16])
        .style(|t, s| {
            let mut style = theme::secondary(t, s);
            style.border.width = 0.0;
            style
        })
        .on_press(Message::Dismiss(id))
    ]
    .spacing(12)
    .align_y(iced::Alignment::Center);
    container(
        column![
            hero,
            row![option(true), option(false)].spacing(18),
            environment,
            Space::new().height(Length::Fill),
            container(Space::new().height(1))
                .width(Length::Fill)
                .style(|_| container::Style {
                    background: Some(theme::LINE.into()),
                    ..Default::default()
                }),
            footer
        ]
        .spacing(16),
    )
    .padding(16)
    .width(Length::Fill)
    .height(Length::Fill)
    .style(|_| container::Style {
        background: Some(Color::WHITE.into()),
        ..Default::default()
    })
    .into()
}

fn environment_rows(app: &App) -> [(String, String); 3] {
    [
        (
            app.t("Software version"),
            format!("V{}", cplus_core::VERSION),
        ),
        (app.t("Operating system"), system_description()),
        (app.t("Interface language"), app.i18n.language.to_string()),
    ]
}

pub(crate) fn environment(app: &App) -> String {
    format!(
        "CPlus Cleaner\n{}\n",
        environment_rows(app)
            .into_iter()
            .map(|(name, value)| format!("{name}: {value}"))
            .collect::<Vec<_>>()
            .join("\n")
    )
}

fn system_description() -> String {
    static NAME: OnceLock<String> = OnceLock::new();
    NAME.get_or_init(|| {
        let key = Key::open(
            HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            KEY_WOW64_64KEY,
            false,
        )
        .ok();
        let product = key
            .as_ref()
            .map(|k| k.string("ProductName"))
            .unwrap_or_default();
        let build = key
            .as_ref()
            .and_then(|k| k.string("CurrentBuildNumber").parse().ok())
            .unwrap_or(0);
        format!(
            "{} · {}",
            system_name(&product, build),
            std::env::consts::ARCH
        )
    })
    .clone()
}

fn system_name(product: &str, build: u32) -> String {
    if product.is_empty() {
        "Windows".into()
    } else if build >= 22000 && product.starts_with("Windows 10") {
        product.replacen("Windows 10", "Windows 11", 1)
    } else {
        product.into()
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn system_name_handles_windows_11_registry_compatibility_without_renaming_servers() {
        assert_eq!(
            super::system_name("Windows 10 Pro", 26100),
            "Windows 11 Pro"
        );
        assert_eq!(
            super::system_name("Windows 10 Home", 19045),
            "Windows 10 Home"
        );
        assert_eq!(
            super::system_name("Windows Server 2025", 26100),
            "Windows Server 2025"
        );
        assert_eq!(super::system_name("", 0), "Windows");
    }
}
