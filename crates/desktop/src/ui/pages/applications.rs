use super::*;
use iced::widget::{Column, button, image, text_input};
use iced::widget::{column, row};
const SIZE_WIDTH: u32 = 112;
const DATE_WIDTH: u32 = 112;
const ACTION_WIDTH: u32 = 116;
const COLUMN_GAP: u32 = 13;

fn heading(app: &App, key: &str, sort: &str, width: u32) -> Element<'static, Message> {
    let arrow = if app.sort == sort && !app.descending {
        "▴"
    } else {
        "▾"
    };
    button(
        row![label(app.t(key)).color(theme::MUTED), small(arrow)]
            .spacing(4)
            .align_y(iced::Alignment::Center),
    )
    .padding(0)
    .width(width)
    .on_press(Message::Sort(sort.into()))
    .style(theme::link)
    .into()
}
pub fn view(app: &App) -> Element<'_, Message> {
    let mut output = column![
        hero(app),
        card(
            row![
                toolbar(app),
                text_input(&app.t("Search applications"), &app.search)
                    .on_input(Message::Search)
                    .style(theme::search_input)
                    .padding(10)
                    .width(320)
            ]
            .spacing(15)
        )
    ]
    .spacing(12);
    let mut rows = Column::new().spacing(1);
    rows = rows.push(
        row![
            container(label("")).width(24),
            Space::new().width(34),
            container(label(app.t("Application")).color(theme::MUTED)).width(Length::Fill),
            heading(
                app,
                if app.page == Feature::Compression {
                    "Before"
                } else if app.page == Feature::AppMover {
                    "Software size"
                } else {
                    "Size"
                },
                "Size",
                SIZE_WIDTH
            ),
            heading(
                app,
                if app.page == Feature::Compression {
                    "After"
                } else {
                    "Date"
                },
                if app.page == Feature::Compression {
                    "After"
                } else {
                    "Date"
                },
                DATE_WIDTH
            ),
            container(label(app.t("Action")).color(theme::MUTED)).width(ACTION_WIDTH)
        ]
        .spacing(COLUMN_GAP)
        .padding([8, 12]),
    );
    if let Some(report) = app.report() {
        let search = app.search.to_lowercase();
        let mut apps = report
            .apps
            .iter()
            .filter(|a| {
                a.name.to_lowercase().contains(&search)
                    || a.path.to_string_lossy().to_lowercase().contains(&search)
            })
            .collect::<Vec<_>>();
        apps.sort_by(|a, b| {
            let order = if app.page == Feature::Compression && app.sort == "After" {
                a.compressed
                    .then_some(a.physical)
                    .cmp(&b.compressed.then_some(b.physical))
            } else if app.page == Feature::Compression && app.sort == "Size" {
                a.physical.cmp(&b.physical)
            } else if app.sort == "Size" {
                a.logical.cmp(&b.logical)
            } else if matches!(app.page, Feature::Uninstall | Feature::AppMover) {
                a.installed.cmp(&b.installed)
            } else {
                a.modified.cmp(&b.modified)
            };
            if app.descending {
                order.reverse()
            } else {
                order
            }
        });
        let selected = app.choices(app.page);
        if apps.is_empty() {
            rows = rows.push(empty(app.t("No matching items")));
        }
        for a in apps {
            let icon: Element<'_, Message> = if let Some(icon) = &a.icon {
                image(image::Handle::from_rgba(
                    icon.width,
                    icon.height,
                    icon.rgba.clone(),
                ))
                .width(34)
                .height(34)
                .into()
            } else if app.page == Feature::Uninstall {
                symbol("application", 32)
            } else {
                nav_icon(app.page, true, 32)
            };
            let (action, label_key) = super::action(app.page);
            let button_name = if app.page == Feature::Uninstall {
                "Uninstall"
            } else {
                label_key
            };
            let identity = a.id.clone();
            let enabled = !app.busy() && a.blocked.is_empty();
            let mut desc = column![
                bold(a.name.clone(), 14),
                small(short(&cplus_core::common::display_path(&a.path), 70))
            ]
            .spacing(5)
            .width(Length::Fill);
            if !a.blocked.is_empty() {
                desc = desc.push(small(app.t(&a.blocked)).color(theme::CORAL));
            }
            let data = row![
                check(
                    selected.contains(&identity),
                    enabled.then_some(Message::Toggle(identity.clone()))
                ),
                container(icon).width(34),
                desc,
                container(label(if app.page == Feature::AppMover && a.logical == 0 {
                    "—".into()
                } else {
                    bytes(if app.page == Feature::Compression {
                        a.physical
                    } else {
                        a.logical
                    })
                }))
                .width(SIZE_WIDTH),
                container(small(
                    if matches!(app.page, Feature::Uninstall | Feature::AppMover) {
                        install_date(&a.installed)
                    } else {
                        if app.page == Feature::Compression {
                            if a.compressed {
                                bytes(a.physical)
                            } else {
                                "—".into()
                            }
                        } else {
                            file_date(a.modified)
                        }
                    }
                ))
                .width(DATE_WIDTH),
                container(btn(
                    app.t(button_name),
                    enabled.then_some(Message::Request(action, Some(identity))),
                    false
                ))
                .width(ACTION_WIDTH)
            ]
            .spacing(COLUMN_GAP)
            .align_y(iced::Alignment::Center);
            rows = rows.push(container(data).padding([14, 12]).style(theme::card));
        }
    } else {
        rows = rows.push(empty(app.t("Scan to see results")));
    }
    output = output.push(content_scroll(rows));
    if app.page == Feature::AppMover {
        output = output.push(footer(app, app.page));
    } else if app.page == Feature::Compression {
        output = output.push(row![
            small(app.t("Compression can slow application startup. Moving apps is preferred.")),
            Space::new().width(Length::Fill),
            link(
                app.t("Compression History"),
                Message::Dialog(Dialog::History(Feature::Compression))
            )
        ]);
    }
    output.height(Length::Fill).into()
}
fn file_date(n: u64) -> String {
    let unix = (n / 10_000_000).saturating_sub(11_644_473_600);
    chrono::DateTime::from_timestamp(unix as i64, 0)
        .map(|d| d.format("%Y-%m-%d").to_string())
        .unwrap_or_default()
}

fn install_date(value: &str) -> String {
    if value.len() == 8 && value.bytes().all(|b| b.is_ascii_digit()) {
        format!("{}-{}-{}", &value[..4], &value[4..6], &value[6..])
    } else {
        value.into()
    }
}
