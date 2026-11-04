use iced::{
    Background, Border, Color, Theme,
    widget::{button, container, progress_bar},
};
pub const INK: Color = Color::from_rgb(0.10, 0.19, 0.23);
pub const MUTED: Color = Color::from_rgb(0.48, 0.56, 0.64);
pub const EMPTY: Color = Color::from_rgb8(0xb3, 0xbb, 0xc3);
pub const MINT: Color = Color::from_rgb(0.12, 0.79, 0.56);
pub const PALE: Color = Color::from_rgb(0.90, 0.98, 0.95);
pub const CORAL: Color = Color::from_rgb(1.0, 0.35, 0.40);
pub const LINE: Color = Color::from_rgb(0.88, 0.92, 0.94);
pub const BG: Color = Color::from_rgb(0.965, 0.98, 0.98);
pub fn toggle(
    theme: &Theme,
    status: iced::widget::toggler::Status,
) -> iced::widget::toggler::Style {
    use iced::widget::toggler::{self, Status};
    let (Status::Active { is_toggled }
    | Status::Hovered { is_toggled }
    | Status::Disabled { is_toggled }) = status;
    let mut style = toggler::default(theme, status);
    style.background = if is_toggled {
        MINT
    } else {
        Color::from_rgb8(0xd9, 0xdd, 0xe3)
    }
    .into();
    style.foreground = Color::WHITE.into();
    style
}
pub fn application() -> Theme {
    Theme::custom(
        "CPlus Cleaner",
        iced::theme::Palette {
            background: BG,
            text: INK,
            primary: MINT,
            success: MINT,
            warning: CORAL,
            danger: CORAL,
        },
    )
}
pub fn input(_: &Theme, _: iced::widget::text_input::Status) -> iced::widget::text_input::Style {
    iced::widget::text_input::Style {
        background: Color::WHITE.into(),
        border: Border {
            color: LINE,
            width: 1.0,
            radius: 8.0.into(),
        },
        icon: MUTED,
        placeholder: MUTED,
        value: INK,
        selection: PALE,
    }
}
pub fn search_input(
    theme: &Theme,
    status: iced::widget::text_input::Status,
) -> iced::widget::text_input::Style {
    let mut style = input(theme, status);
    if matches!(status, iced::widget::text_input::Status::Focused { .. }) {
        style.placeholder = Color::TRANSPARENT;
    }
    style
}
pub fn select(_: &Theme, _: iced::widget::pick_list::Status) -> iced::widget::pick_list::Style {
    iced::widget::pick_list::Style {
        text_color: INK,
        placeholder_color: MUTED,
        handle_color: MINT,
        background: Color::WHITE.into(),
        border: Border {
            color: LINE,
            width: 1.0,
            radius: 8.0.into(),
        },
    }
}
pub fn settings_select(
    theme: &Theme,
    status: iced::widget::pick_list::Status,
) -> iced::widget::pick_list::Style {
    let mut style = select(theme, status);
    style.handle_color = INK;
    style
}
pub fn settings_menu(_: &Theme) -> iced::widget::overlay::menu::Style {
    iced::widget::overlay::menu::Style {
        background: Color::WHITE.into(),
        border: Border {
            color: LINE,
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: INK,
        selected_text_color: INK,
        selected_background: PALE.into(),
        shadow: Default::default(),
    }
}
pub fn card(_: &Theme) -> container::Style {
    container::Style {
        background: Some(Color::WHITE.into()),
        border: Border {
            color: LINE,
            width: 1.0,
            radius: 12.0.into(),
        },
        text_color: Some(INK),
        ..Default::default()
    }
}
pub fn hero(_: &Theme) -> container::Style {
    let gradient = iced::gradient::Linear::new(iced::Radians(0.55))
        .add_stop(0.0, Color::WHITE)
        .add_stop(1.0, PALE);
    container::Style {
        background: Some(Background::Gradient(gradient.into())),
        border: Border {
            color: LINE,
            width: 1.0,
            radius: 12.0.into(),
        },
        text_color: Some(INK),
        ..Default::default()
    }
}
pub fn surface(_: &Theme) -> container::Style {
    container::Style {
        background: Some(BG.into()),
        text_color: Some(INK),
        ..Default::default()
    }
}
pub fn primary(_: &Theme, status: button::Status) -> button::Style {
    let disabled = matches!(status, button::Status::Disabled);
    button::Style {
        background: Some(
            if disabled {
                LINE
            } else if matches!(status, button::Status::Hovered) {
                Color::from_rgb(0.1, 0.72, 0.50)
            } else {
                MINT
            }
            .into(),
        ),
        text_color: if disabled { MUTED } else { Color::WHITE },
        border: Border {
            radius: 9.0.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}
pub fn secondary(_: &Theme, status: button::Status) -> button::Style {
    let disabled = matches!(status, button::Status::Disabled);
    button::Style {
        background: Some(if disabled { BG } else { PALE }.into()),
        text_color: if disabled { MUTED } else { MINT },
        border: Border {
            radius: 9.0.into(),
            color: if disabled { LINE } else { MINT },
            width: 1.0,
        },
        ..Default::default()
    }
}
pub fn subtle(theme: &Theme, status: button::Status) -> button::Style {
    button::Style {
        background: Some(
            match status {
                button::Status::Disabled | button::Status::Active => {
                    Color::from_rgb8(0xf6, 0xfa, 0xfa)
                }
                button::Status::Hovered | button::Status::Pressed => PALE,
            }
            .into(),
        ),
        ..secondary(theme, status)
    }
}
pub fn scan(theme: &Theme, status: button::Status) -> button::Style {
    let mut style = subtle(theme, status);
    match status {
        button::Status::Active => {
            style.background = Some(Color::from_rgb8(0xe6, 0xfa, 0xf2).into())
        }
        button::Status::Hovered | button::Status::Pressed => {
            style.background = Some(Color::from_rgb8(0xd2, 0xf3, 0xe4).into());
        }
        button::Status::Disabled => {}
    }
    style
}
pub fn link(_: &Theme, status: button::Status) -> button::Style {
    button::Style {
        background: if matches!(status, button::Status::Hovered) {
            Some(PALE.into())
        } else {
            None
        },
        text_color: MINT,
        border: Border {
            radius: 7.0.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}
pub fn menu_item(theme: &Theme, status: button::Status) -> button::Style {
    button::Style {
        text_color: INK,
        ..link(theme, status)
    }
}
pub fn progress(_: &Theme) -> progress_bar::Style {
    progress_bar::Style {
        background: LINE.into(),
        bar: MINT.into(),
        border: Border {
            radius: 6.0.into(),
            ..Default::default()
        },
    }
}
pub fn capacity(_: &Theme) -> progress_bar::Style {
    progress_bar::Style {
        background: LINE.into(),
        bar: CORAL.into(),
        border: Border {
            radius: 6.0.into(),
            ..Default::default()
        },
    }
}
