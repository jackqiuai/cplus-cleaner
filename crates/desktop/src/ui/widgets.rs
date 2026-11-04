use super::{fonts, theme};
use crate::app::Message;
use iced::{
    Element, Length,
    widget::{Column, Row, button, container, image, svg, text},
};
pub fn label(value: impl Into<String>) -> iced::widget::Text<'static> {
    text(cplus_core::common::display_text(&value.into()))
        .font(fonts::regular())
        .shaping(iced::widget::text::Shaping::Advanced)
        .size(14)
        .color(theme::INK)
}
pub fn small(value: impl Into<String>) -> iced::widget::Text<'static> {
    text(cplus_core::common::display_text(&value.into()))
        .font(fonts::regular())
        .shaping(iced::widget::text::Shaping::Advanced)
        .size(12)
        .color(theme::MUTED)
}
pub fn bold(value: impl Into<String>, size: u32) -> iced::widget::Text<'static> {
    text(cplus_core::common::display_text(&value.into()))
        .size(size)
        .font(fonts::bold())
        .shaping(iced::widget::text::Shaping::Advanced)
        .color(theme::INK)
}
pub fn amount(value: impl Into<String>, size: u32) -> iced::widget::Text<'static> {
    bold(value, size).color(theme::CORAL)
}
pub fn btn(
    value: impl Into<String>,
    action: Option<Message>,
    primary: bool,
) -> Element<'static, Message> {
    button(bold(value, 14).color(if primary {
        iced::Color::WHITE
    } else {
        theme::MINT
    }))
    .padding([11, 24])
    .on_press_maybe(action)
    .style(if primary {
        theme::primary
    } else {
        theme::secondary
    })
    .into()
}
pub fn action_btn(
    value: impl Into<String>,
    action: Option<Message>,
    primary: bool,
    width: u32,
) -> Element<'static, Message> {
    button(
        bold(value, 14)
            .color(if primary {
                iced::Color::WHITE
            } else {
                theme::MINT
            })
            .width(Length::Fill)
            .align_x(iced::Alignment::Center),
    )
    .width(width)
    .padding([11, 24])
    .on_press_maybe(action)
    .style(if primary { theme::primary } else { theme::scan })
    .into()
}
pub fn link(value: impl Into<String>, message: Message) -> Element<'static, Message> {
    button(label(value).color(theme::MINT))
        .padding([5, 8])
        .on_press(message)
        .style(theme::link)
        .into()
}
pub fn menu_item(value: impl Into<String>, message: Message) -> Element<'static, Message> {
    button(label(value))
        .padding([12, 14])
        .width(Length::Fill)
        .on_press(message)
        .style(theme::menu_item)
        .into()
}
pub fn card<'a>(content: impl Into<Element<'a, Message>>) -> Element<'a, Message> {
    container(content)
        .padding(18)
        .width(Length::Fill)
        .style(theme::card)
        .into()
}
pub fn bitmap(path: &str, size: u32) -> Element<'static, Message> {
    if let Some(bytes) = cplus_core::resource(&format!("assets/{path}")) {
        image(image::Handle::from_bytes(bytes))
            .width(size)
            .height(size)
            .content_fit(iced::ContentFit::Contain)
            .into()
    } else {
        container(label("◇").size(size))
            .width(size)
            .height(size)
            .center(Length::Fill)
            .into()
    }
}
pub fn nav_icon(
    feature: cplus_core::Feature,
    active: bool,
    size: u32,
) -> Element<'static, Message> {
    let path = match feature {
        cplus_core::Feature::Home => "<path d='M3 10 12 3l9 7v11h-6v-7H9v7H3Z'/>",
        cplus_core::Feature::Drive => {
            "<path d='M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7'/>"
        }
        cplus_core::Feature::Chat => {
            "<path d='M21 11a8 7 0 0 1-8 7H9l-5 3 1-6a7 7 0 0 1-2-5 8 7 0 0 1 18 1Z'/><path d='M8 11h.1M12 11h.1M16 11h.1' stroke-width='2.7'/>"
        }
        cplus_core::Feature::LargeFiles => {
            "<path d='M3 6h7l2 3h9v12H3ZM16 13H7m0 0 3-3m-3 3 3 3'/>"
        }
        cplus_core::Feature::AppMover => {
            "<path d='m12 2 10 5-10 5L2 7Zm0 10v10M2 7v10l10 5 10-5V7M16 16h6m-2-2 2 2-2 2'/>"
        }
        cplus_core::Feature::Compression => {
            "<path d='M5 2h10l4 4v16H5ZM15 2v5h4M11 4v2m2 0v2m-2 0v2m2 0v2m-2 0v2m0 2h3v4h-3Z'/>"
        }
        cplus_core::Feature::Duplicates => {
            "<rect x='7' y='6' width='14' height='16' rx='2'/><path d='M16 2H3v16m8-7h6m-6 4h6'/>"
        }
        cplus_core::Feature::Privacy => {
            "<path d='m12 2 9 4v6c0 5-6 9-9 10-3-1-9-5-9-10V6Zm-4 9 3 3 5-6'/>"
        }
        cplus_core::Feature::Uninstall => {
            "<path d='M3 5h18M9 5V2h6v3M6 5l1 17h10l1-17m-8 5 4 5m0-5-4 5'/>"
        }
        cplus_core::Feature::Popup => {
            "<rect x='2' y='3' width='20' height='16' rx='2'/><path d='M2 8h20'/><circle cx='17' cy='17' r='5'/><path d='m14 14 6 6'/>"
        }
        cplus_core::Feature::System => {
            "<path d='m10 2 4 0 1 3 3 1 3-1 2 4-2 2v3l2 2-2 4-3-1-3 1-1 3h-4l-1-3-3-1-3 1-2-4 2-2v-3L1 9l2-4 3 1 3-1Z'/><circle cx='12' cy='12' r='4'/>"
        }
    };
    let color = if active { "#13bd86" } else { "#589399" };
    let markup = format!(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{color}' stroke-width='1.65' stroke-linecap='round' stroke-linejoin='round'>{path}</svg>"
    );
    svg(svg::Handle::from_memory(markup.into_bytes()))
        .width(size)
        .height(size)
        .into()
}
pub fn logo(size: u32) -> Element<'static, Message> {
    let bytes = cplus_core::resource("assets/brand/logo.svg").unwrap_or_default();
    svg(svg::Handle::from_memory(bytes))
        .width(size)
        .height(size)
        .into()
}
pub fn check(checked: bool, action: Option<Message>) -> Element<'static, Message> {
    button(bitmap(
        if checked {
            "check-on.png"
        } else {
            "check-off.png"
        },
        18,
    ))
    .padding(3)
    .style(theme::link)
    .on_press_maybe(action)
    .into()
}
pub fn bytes(n: u64) -> String {
    if n >= 1024 * 1024 * 1024 {
        format!("{:.2} GB", n as f64 / 1073741824.0)
    } else if n >= 1024 * 1024 {
        format!("{:.2} MB", n as f64 / 1048576.0)
    } else if n >= 1024 {
        format!("{:.1} KB", n as f64 / 1024.0)
    } else {
        format!("{n} B")
    }
}
pub fn short(value: &str, max: usize) -> String {
    let mut iter = value.chars();
    let prefix = iter.by_ref().take(max).collect::<String>();
    if iter.next().is_some() {
        format!("{prefix}…")
    } else {
        prefix
    }
}
pub fn table_header<'a>(
    items: Vec<(&str, u16)>,
    t: impl Fn(&str) -> String,
) -> Element<'a, Message> {
    let mut r = Row::new().spacing(12);
    for (label, width) in items {
        r = r.push(container(small(t(label))).width(Length::FillPortion(width)));
    }
    container(r)
        .padding([10, 14])
        .style(|_| container::Style {
            background: Some(theme::BG.into()),
            ..Default::default()
        })
        .into()
}
pub fn empty(message: impl Into<String>) -> Element<'static, Message> {
    container(small(message).color(theme::EMPTY))
        .width(Length::Fill)
        .padding(55)
        .center_x(Length::Fill)
        .into()
}
pub fn symbol(name: &str, size: u32) -> Element<'static, Message> {
    let bytes = cplus_core::resource(&format!("assets/ui/{name}.svg")).unwrap_or_default();
    svg(svg::Handle::from_memory(bytes))
        .width(size)
        .height(size)
        .into()
}
pub fn grid<'a>(elements: Vec<Element<'a, Message>>, columns: usize) -> Column<'a, Message> {
    let mut output = Column::new().spacing(12);
    let mut current = Row::new().spacing(12);
    let mut count = 0;
    for element in elements {
        current = current.push(container(element).width(Length::FillPortion(1)));
        count += 1;
        if count == columns {
            output = output.push(current);
            current = Row::new().spacing(12);
            count = 0;
        }
    }
    if count > 0 {
        for _ in count..columns {
            current = current.push(iced::widget::Space::new().width(Length::FillPortion(1)));
        }
        output = output.push(current);
    }
    output
}
pub fn ring(used: f64, drive: &str, size: f32) -> Element<'static, Message> {
    let circumference = 2.0 * std::f64::consts::PI * 73.0;
    let dash = used.clamp(0.0, 1.0) * circumference;
    let markup = format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180"><defs><linearGradient id="coral" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#ff6f7c"/><stop offset="1" stop-color="#ff5267"/></linearGradient></defs><circle cx="90" cy="90" r="87" fill="white" opacity=".78"/><circle cx="90" cy="90" r="73" fill="white" stroke="#ffdde3" stroke-width="21"/><circle cx="90" cy="90" r="73" fill="none" stroke="url(#coral)" stroke-width="21" stroke-linecap="round" stroke-dasharray="{dash} {circumference}" transform="rotate(-90 90 90)"/></svg>"##,
    );
    iced::widget::stack![
        svg(svg::Handle::from_memory(markup.into_bytes()))
            .width(size)
            .height(size),
        container(
            bold(drive, 15)
                .width(size * 0.6)
                .align_x(iced::Alignment::Center)
        )
        .center(size)
    ]
    .width(size)
    .height(size)
    .into()
}

pub fn result_check(
    checked: bool,
    partial: bool,
    action: Option<Message>,
) -> Element<'static, Message> {
    button(symbol(
        if partial {
            "result-check-partial"
        } else if checked {
            "result-check-on"
        } else {
            "result-check-off"
        },
        20,
    ))
    .padding(2)
    .style(theme::link)
    .on_press_maybe(action)
    .into()
}
