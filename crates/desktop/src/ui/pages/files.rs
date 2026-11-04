use super::*;
use iced::widget::{button, text_input};
use iced::widget::{column, row};
pub fn view(app: &App) -> Element<'_, Message> {
    let f = app.page;
    let mut output = column![hero(app)].spacing(12);
    if f == Feature::LargeFiles && app.report().is_none() {
        return output
            .push(content_scroll(
                column![
                    row![disk_card(app, false), disk_card(app, true)].spacing(14),
                    empty(app.t("Move large files to another drive"))
                ]
                .spacing(20),
            ))
            .push(footer(app, f))
            .into();
    }
    output = output.push(card(
        row![
            toolbar(app),
            text_input(&app.t("Search files"), &app.search)
                .on_input(Message::Search)
                .style(theme::search_input)
                .padding(9)
                .width(300)
        ]
        .spacing(10),
    ));
    if f == Feature::LargeFiles
        && let Some(report) = app.report()
    {
        let skipped = report.large_scan.as_ref().map(|s| s.skipped).unwrap_or(0);
        output = output.push(
            row![
                small(format!(
                    "{} {}",
                    app.t("Disk usage"),
                    bytes(report.allocated_bytes())
                )),
                small(format!(
                    "{} {}",
                    app.t("Estimated reclaimable"),
                    bytes(report.reclaimable_bytes())
                )),
                small(format!("{} {}", app.t("Skipped"), skipped))
            ]
            .spacing(16),
        );
    }
    output = output.push(content_scroll(result_list(app)));
    output.push(footer(app, f)).height(Length::Fill).into()
}

const SIZE_WIDTH: u32 = 112;
const ACTION_WIDTH: u32 = 188;

pub(super) fn result_action(
    app: &App,
    key: &str,
    action: Option<Message>,
) -> Element<'static, Message> {
    button(
        label(app.t(if key == "Move" { "Migrate" } else { "删除" }))
            .size(12)
            .color(theme::MINT),
    )
    .padding([5, 10])
    .width(72)
    .style(|theme, status| {
        let mut style = theme::secondary(theme, status);
        style.border.radius = 6.0.into();
        if matches!(status, iced::widget::button::Status::Active) {
            style.background = Some(iced::Color::WHITE.into());
        }
        style
    })
    .on_press_maybe(action)
    .into()
}

pub(super) fn separator() -> Element<'static, Message> {
    container(Space::new().height(1))
        .width(Length::Fill)
        .style(|_| iced::widget::container::Style {
            background: Some(theme::LINE.into()),
            ..Default::default()
        })
        .into()
}

pub(super) fn file_icon(path: &std::path::Path) -> Element<'static, Message> {
    let extension = path
        .extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_uppercase();
    iced::widget::stack![
        symbol("result-file", 28),
        container(bold(short(&extension, 4), 7).color(iced::Color::WHITE))
            .width(28)
            .height(28)
            .align_x(iced::alignment::Horizontal::Center)
            .align_y(iced::alignment::Vertical::Bottom)
    ]
    .width(28)
    .height(28)
    .into()
}

fn result_list(app: &App) -> Element<'_, Message> {
    let Some(report) = app.report() else {
        return empty(app.t("Scan to see results"));
    };
    let selected = app.choices(Feature::LargeFiles);
    let eligible: Vec<_> = report
        .files
        .iter()
        .filter(|f| f.blocked.is_empty())
        .collect();
    let checked = !eligible.is_empty() && eligible.iter().all(|f| selected.contains(&f.id));
    let mut rows = column![
        container(
            row![
                result_check(
                    checked,
                    !checked && eligible.iter().any(|f| selected.contains(&f.id)),
                    (!app.busy() && !eligible.is_empty()).then_some(Message::All(!checked))
                ),
                Space::new().width(20),
                Space::new().width(28),
                container(small(app.t("Name"))).width(Length::Fill),
                container(small(app.t("Size"))).width(SIZE_WIDTH),
                container(small(app.t("Action"))).width(ACTION_WIDTH)
            ]
            .spacing(10)
            .align_y(iced::Alignment::Center)
        )
        .padding([8, 12])
        .style(theme::surface)
    ]
    .spacing(0);
    let query = app.search.to_lowercase();
    let mut visible = false;
    for category in &report.categories {
        let files: Vec<_> = report
            .files
            .iter()
            .filter(|f| f.category == category.id)
            .collect();
        let matching: Vec<_> = files
            .iter()
            .filter(|f| {
                query.is_empty()
                    || f.path.to_string_lossy().to_lowercase().contains(&query)
                    || category_name(app, &category.name)
                        .to_lowercase()
                        .contains(&query)
            })
            .collect();
        if matching.is_empty() {
            continue;
        }
        visible = true;
        let open = app.expanded.contains(&category.id);
        let allowed: Vec<_> = files.iter().filter(|f| f.blocked.is_empty()).collect();
        let checked = !allowed.is_empty() && allowed.iter().all(|f| selected.contains(&f.id));
        let selected_in_group = allowed.iter().any(|f| selected.contains(&f.id));
        let enabled = !app.busy() && selected_in_group;
        rows = rows.push(
            container(
                row![
                    result_check(
                        checked,
                        !checked && selected_in_group,
                        (!app.busy() && !allowed.is_empty())
                            .then_some(Message::Group(category.id.clone()))
                    ),
                    button(label(if open { "⌄" } else { "›" }).size(19))
                        .padding(0)
                        .width(20)
                        .style(theme::link)
                        .on_press(Message::Expand(category.id.clone())),
                    button(symbol("result-folder", 28))
                        .padding(0)
                        .style(theme::link)
                        .on_press(Message::Expand(category.id.clone())),
                    button(
                        row![
                            bold(category_name(app, &category.name), 14),
                            small(format!("({} {})", category.count, app.t("Files")))
                        ]
                        .spacing(6)
                        .align_y(iced::Alignment::Center)
                    )
                    .padding(0)
                    .style(theme::link)
                    .width(Length::Fill)
                    .on_press(Message::Expand(category.id.clone())),
                    container(amount(bytes(category.bytes), 15)).width(SIZE_WIDTH),
                    container(
                        row![
                            result_action(
                                app,
                                "Delete",
                                enabled.then_some(Message::Request(
                                    Action::Delete,
                                    Some(category.id.clone())
                                ))
                            ),
                            result_action(
                                app,
                                "Move",
                                enabled.then_some(Message::Request(
                                    Action::Move,
                                    Some(category.id.clone())
                                ))
                            ),
                            Space::new().width(24)
                        ]
                        .spacing(8)
                    )
                    .width(ACTION_WIDTH)
                ]
                .spacing(10)
                .align_y(iced::Alignment::Center),
            )
            .padding([9, 12]),
        );
        rows = rows.push(separator());
        if !open {
            continue;
        }
        for file in matching {
            let enabled = !app.busy() && file.blocked.is_empty();
            let name = file.path.file_name().unwrap_or_default().to_string_lossy();
            let mut description = column![iced::widget::tooltip(
                label(short(&name, 50)).size(13),
                container(small(cplus_core::common::display_path(&file.path)))
                    .padding(8)
                    .style(theme::surface),
                iced::widget::tooltip::Position::Top
            )]
            .width(Length::Fill);
            if !file.blocked.is_empty() {
                description = description.push(small(app.t(&file.blocked)).color(theme::CORAL));
            }
            rows = rows.push(
                container(
                    row![
                        Space::new().width(34),
                        result_check(
                            selected.contains(&file.id),
                            false,
                            enabled.then_some(Message::Toggle(file.id.clone()))
                        ),
                        file_icon(&file.path),
                        description,
                        container(small(bytes(file.size))).width(SIZE_WIDTH),
                        container(
                            row![
                                result_action(
                                    app,
                                    "Delete",
                                    enabled.then_some(Message::Request(
                                        Action::Delete,
                                        Some(file.id.clone())
                                    ))
                                ),
                                result_action(
                                    app,
                                    "Move",
                                    enabled.then_some(Message::Request(
                                        Action::Move,
                                        Some(file.id.clone())
                                    ))
                                ),
                                button(symbol("result-open-folder", 18))
                                    .padding(3)
                                    .style(theme::link)
                                    .on_press(Message::Reveal(file.path.clone()))
                            ]
                            .spacing(8)
                        )
                        .width(ACTION_WIDTH)
                    ]
                    .spacing(10)
                    .align_y(iced::Alignment::Center),
                )
                .padding([8, 12]),
            );
            rows = rows.push(separator());
        }
    }
    if !visible {
        rows = rows.push(empty(app.t("No matching items")));
    }
    container(rows)
        .width(Length::Fill)
        .style(theme::card)
        .into()
}
pub(super) fn category_name(app: &App, name: &str) -> String {
    app.t(match name {
        "archives" => "Archives",
        "installers" => "Installers",
        "logs" => "Logs",
        "videos" => "Videos",
        "audio" => "Audio",
        "documents" => "Documents",
        "images" => "Images",
        "other" => "Other files",
        "temporary" => "Temporary files",
        _ => name,
    })
}
