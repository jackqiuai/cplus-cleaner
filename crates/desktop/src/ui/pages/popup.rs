use super::*;
use iced::widget::{Column, toggler};
use iced::widget::{column, row};
pub fn view(app: &App) -> Element<'_, Message> {
    let engine = app.engine.lock().unwrap_or_else(|e| e.into_inner());
    let count = engine.groups.iter().map(|g| g.rules.len()).sum::<usize>();
    let hero = container(
        row![
            bitmap("status-warning.png", 32),
            column![
                row![
                    bold(app.t("Found"), 20),
                    amount(engine.groups.len().to_string(), 28),
                    label(app.t("apps")),
                    amount(count.to_string(), 28),
                    label(app.t("Advertising windows"))
                ]
                .spacing(9)
                .align_y(iced::Alignment::Center),
                small(app.t("Goodbye to popup distractions"))
            ]
            .spacing(10)
            .width(Length::Fill),
            main_button(app, "Scan", (!app.busy()).then_some(Message::Scan), false),
            main_button(
                app,
                "Block all",
                (!app.busy() && count > 0).then_some(Message::PopupToggle("*".into(), true)),
                true
            )
        ]
        .spacing(16)
        .align_y(iced::Alignment::Center),
    )
    .padding(24)
    .style(theme::hero);
    let mut body = Column::new().spacing(10);
    body = body.push(table_header(
        vec![
            ("Source", 4),
            ("Position", 2),
            ("Blocked", 2),
            ("Toggle", 2),
        ],
        |s| app.t(s),
    ));
    for group in &engine.groups {
        let icon = group
            .icon
            .as_ref()
            .map(|i| {
                iced::widget::image(iced::widget::image::Handle::from_rgba(
                    i.width,
                    i.height,
                    i.rgba.clone(),
                ))
                .width(30)
                .height(30)
                .into()
            })
            .unwrap_or_else(|| nav_icon(Feature::Popup, true, 30));
        let name = group.name.clone();
        let key = group.key.clone();
        let on = engine.enabled(&key);
        let count = group
            .rules
            .iter()
            .map(|r| engine.state.counts.get(&r.id).copied().unwrap_or(0))
            .sum::<u64>();
        let header = row![
            icon,
            label(format!(
                "{}  {}",
                if app.expanded.contains(&key) {
                    "⌄"
                } else {
                    "›"
                },
                name
            )),
            Space::new().width(Length::Fill),
            small(format!("{} {}", group.rules.len(), app.t("items"))),
            container(label(count.to_string())).width(90),
            toggler(on)
                .style(theme::toggle)
                .on_toggle(move |on| Message::PopupToggle(key.clone(), on))
        ]
        .spacing(14)
        .align_y(iced::Alignment::Center);
        let header = iced::widget::mouse_area(container(header).padding(18).width(Length::Fill))
            .on_press(Message::Expand(group.key.clone()))
            .interaction(iced::mouse::Interaction::Pointer);
        let mut content = column![header].spacing(0);
        if app.expanded.contains(&group.key) {
            for rule in &group.rules {
                let key = rule.id.clone();
                content = content.push(
                    container(
                        row![
                            label(app.t(&rule.label)).width(Length::FillPortion(4)),
                            small(app.t(match rule.position {
                                0 => "Full screen",
                                1 => "Top left",
                                2 => "Bottom left",
                                3 => "Center",
                                4 => "Top right",
                                5 => "Bottom right",
                                _ => "Any position",
                            }))
                            .width(Length::FillPortion(2)),
                            label(engine.state.counts.get(&key).unwrap_or(&0).to_string())
                                .width(Length::FillPortion(2)),
                            toggler(engine.enabled(&key))
                                .style(theme::toggle)
                                .on_toggle(move |on| Message::PopupToggle(key.clone(), on))
                        ]
                        .spacing(16)
                        .align_y(iced::Alignment::Center),
                    )
                    .padding([7, 34]),
                );
            }
        }
        body = body.push(container(content).width(Length::Fill).style(theme::card));
    }
    if engine.groups.is_empty() {
        body = body.push(empty(app.t("Scan to see results")));
    }
    column![
        hero,
        content_scroll(body),
        row![
            Space::new().width(Length::Fill),
            link(
                app.t("Blocking History"),
                Message::Dialog(Dialog::History(Feature::Popup))
            )
        ]
    ]
    .spacing(15)
    .height(Length::Fill)
    .into()
}
