use super::*;
use iced::{
    Color,
    widget::{column, pick_list, svg, text_input, toggler},
};

pub(super) fn view(app: &App) -> Element<'_, Message> {
    let language = if app.edit.language.is_empty() {
        app.i18n.language
    } else {
        Language::from_code(&app.edit.language)
    };
    let icon = svg(svg::Handle::from_memory(br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" fill="none" stroke="#0bb47e" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="m13 3 6 0 1 4 3 2 4-1 3 5-3 3v3l3 3-3 5-4-1-3 2-1 3h-6l-1-3-3-2-4 1-3-5 3-3v-3l-3-3 3-5 4 1 3-2Z" transform="translate(0 -1) scale(.95)"/><circle cx="15.7" cy="15.7" r="4.7"/></svg>"##))
        .width(28).height(28);
    let header = row![
        container(icon).center(46).style(|_| container::Style {
            background: Some(theme::PALE.into()),
            border: iced::Border {
                radius: 10.0.into(),
                ..Default::default()
            },
            ..Default::default()
        }),
        column![
            bold(app.t("Settings"), 24),
            small(app.t("Choose how cleanup and windows behave")).size(12)
        ]
        .spacing(5)
    ]
    .spacing(14)
    .align_y(iced::Alignment::Center);
    let toggle = |name: &str, description: &str, key: &'static str, on: bool| {
        row![
            column![bold(app.t(name), 15), small(app.t(description)).size(12)]
                .spacing(4)
                .width(Length::Fill),
            toggler(on)
                .size(24)
                .on_toggle(move |on| Message::Setting(key, on))
                .style(theme::toggle)
        ]
        .spacing(14)
        .align_y(iced::Alignment::Center)
    };
    let mut time = text_input("HH:MM", &app.edit.cleanup_time)
        .size(13)
        .padding([7, 10])
        .width(104)
        .style(|t, status| {
            let mut style = theme::input(t, status);
            if matches!(status, iced::widget::text_input::Status::Disabled) {
                style.background = theme::BG.into();
                style.value = theme::MUTED;
            }
            style
        });
    if app.edit.scheduled_cleanup {
        time = time
            .on_input(Message::Time)
            .on_submit(Message::SaveSettings);
    }
    let separator = || {
        container(Space::new().height(1))
            .width(Length::Fill)
            .style(|_| container::Style {
                background: Some(theme::LINE.into()),
                ..Default::default()
            })
    };
    let mut content = column![
        row![
            bold(app.t("Language"), 15).width(Length::Fill),
            pick_list(Language::ALL, Some(language), Message::Language)
                .width(200)
                .text_size(14)
                .padding([7, 10])
                .style(theme::settings_select)
                .menu_style(theme::settings_menu)
        ]
        .spacing(14)
        .align_y(iced::Alignment::Center),
        separator(),
        toggle(
            "Start with Windows",
            "Start after signing in to Windows",
            "autostart",
            app.edit.autostart
        ),
        separator(),
        column![
            toggle(
                "Scheduled cleanup",
                "Delete the default drive cleanup categories every day",
                "schedule",
                app.edit.scheduled_cleanup
            ),
            row![
                small(app.t("Every day")).size(12),
                time,
                small(app.t("When enabled, default cleanup items will be deleted directly."))
                    .size(11)
                    .width(Length::Fill)
            ]
            .spacing(10)
            .align_y(iced::Alignment::Center)
        ]
        .spacing(10),
        separator(),
        toggle(
            "Hide to tray",
            "Keep running after the main window is closed",
            "tray",
            app.edit.close_to_tray
        ),
        separator(),
        toggle(
            "System status",
            "Show CPU, memory, disk and network speed",
            "monitor",
            app.edit.taskbar_monitor
        )
    ]
    .spacing(12);
    if app.edit.validate().is_err() {
        content = content.push(small(app.t("Invalid cleanup time; use HH:MM")).color(theme::CORAL));
    }
    container(
        column![
            header,
            container(content)
                .padding(16)
                .width(Length::Fill)
                .style(theme::card)
        ]
        .spacing(14),
    )
    .padding(16)
    .height(Length::Fill)
    .width(Length::Fill)
    .style(|_| container::Style {
        background: Some(Color::WHITE.into()),
        ..Default::default()
    })
    .into()
}
