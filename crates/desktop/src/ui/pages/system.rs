use super::*;
use cplus_core::features::system_slim::Action as S;
use iced::widget::{column, row};
pub fn view(app: &App) -> Element<'_, Message> {
    let mut cards = vec![];
    let definitions = [
        (
            "recycle_bin",
            "System recycle bin",
            vec![("Empty recycle bin", S::EmptyRecycleBin)],
        ),
        (
            "hibernation",
            "Hibernation file",
            vec![
                ("Disable hibernation", S::HibernateOff),
                ("Enable hibernation", S::HibernateOn),
            ],
        ),
        (
            "pagefile",
            "Virtual memory",
            vec![("Virtual memory settings", S::PagefileSettings)],
        ),
        ("swapfile", "Application swap file", vec![]),
        (
            "components",
            "Windows update components",
            vec![
                ("Analyze components", S::AnalyzeComponents),
                ("Clean components", S::CleanupComponents),
            ],
        ),
    ];
    for (id, name, actions) in definitions {
        let item = app
            .report()
            .and_then(|r| r.system.iter().find(|i| i.id == id));
        let mut r = row![
            symbol(id, 39),
            column![
                bold(app.t(name), 20),
                small(app.t(if id == "pagefile" {
                    "Changes may require a restart."
                } else {
                    "Managed by Windows"
                }))
            ]
            .spacing(7)
            .width(Length::Fill),
            amount(
                item.and_then(|i| i.bytes)
                    .map(bytes)
                    .unwrap_or_else(|| "—".into()),
                23
            )
        ]
        .spacing(20)
        .align_y(iced::Alignment::Center);
        for (name, action) in actions {
            let enabled = !app.busy()
                && !(action == S::CleanupComponents && app.component_analysis.is_empty());
            r = r.push(btn(
                app.t(name),
                enabled.then_some(Message::Request(Action::System(action), None)),
                false,
            ));
        }
        cards.push(card(r));
    }
    let mut body = column![
        card(
            row![
                nav_icon(Feature::System, true, 42),
                small(app.t("These Windows-managed items are separate from ordinary cleanup.")),
                Space::new().width(Length::Fill)
            ]
            .spacing(15)
            .align_y(iced::Alignment::Center)
        ),
        disk_card(app, false)
    ]
    .spacing(13);
    for c in cards {
        body = body.push(c);
    }
    if !app.component_analysis.is_empty() {
        body = body.push(card(
            column![
                bold(app.t("Analyze components"), 16),
                label(short(&app.component_analysis, 2200))
            ]
            .spacing(10),
        ));
    }
    content_scroll(body)
}
