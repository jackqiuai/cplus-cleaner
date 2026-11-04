use super::*;
use iced::widget::{column, responsive, row, stack, svg};
use std::collections::BTreeSet;

pub fn view(app: &App) -> Element<'_, Message> {
    // The parent measures the job footer first; fit both tool rows in what remains.
    responsive(move |size| content(app, size)).into()
}

fn content(app: &App, size: iced::Size) -> Element<'_, Message> {
    let compact = size.height < 580.0 || size.width < 850.0;
    let gap = if compact { 12 } else { 16 };
    let mut unique = BTreeSet::new();
    let cleanable = [Feature::Drive, Feature::Chat]
        .iter()
        .filter_map(|f| app.reports.get(f))
        .flat_map(|r| r.files.iter())
        .filter(|f| unique.insert(cplus_core::common::key(&f.path)))
        .map(|f| f.size)
        .sum::<u64>()
        + app
            .reports
            .get(&Feature::Drive)
            .map(|r| {
                r.categories
                    .iter()
                    .filter(|c| {
                        matches!(
                            c.id.as_str(),
                            "drive:windows_delivery"
                                | "drive:windows_updates"
                                | "drive:windows_old"
                        )
                    })
                    .map(|c| c.bytes)
                    .sum::<u64>()
            })
            .unwrap_or(0);
    let ready = app
        .reports
        .get(&Feature::Drive)
        .is_some_and(|r| !r.token.is_empty());
    let scanning = app
        .job
        .as_ref()
        .is_some_and(|job| job.name == "Scanning" && job.ctx.feature == Feature::Home);
    let summary = column![
        bold(
            if scanning {
                app.t("Scanning")
            } else if ready {
                completion_text(
                    app,
                    if app.scan_elapsed.contains_key(&Feature::Home) {
                        Feature::Home
                    } else {
                        Feature::Drive
                    },
                )
            } else {
                app.t("Ready to scan")
            },
            15
        )
        .color(theme::MINT),
        bold(app.t("Space found"), 25),
        amount(
            if ready {
                bytes(cleanable)
            } else {
                "—".into()
            },
            46
        ),
        small(app.t("Start with a scan to review what can be cleaned.")),
        row![
            main_button(app, "Scan", (!app.busy()).then_some(Message::Scan), false),
            main_button(
                app,
                "Manage now",
                Some(Message::Navigate(Feature::Drive)),
                true
            )
        ]
        .spacing(12)
    ]
    .spacing(13)
    .width(Length::Fill);

    let horizontal_padding = 24.0;
    let hero_height = if compact { 242 } else { 274 };
    let ring_size = (size.width * 0.185).clamp(142.0, 196.0);
    let capacity_width = if size.width < 850.0 { 124 } else { 140 };
    let hero_gap = if size.width < 850.0 { 8.0 } else { 14.0 };
    let summary_min = if app.i18n.language == crate::i18n::Language::English {
        300.0
    } else {
        248.0
    };
    let artwork_size = (size.width * 0.27).clamp(180.0, 280.0).min(
        size.width
            - horizontal_padding * 2.0
            - summary_min
            - ring_size
            - capacity_width as f32
            - hero_gap * 3.0,
    );
    let mut foreground = row![summary]
        .spacing(hero_gap)
        .align_y(iced::Alignment::Center);
    if let Some(disk) = app.drive() {
        let disk_label = if app.i18n.language == crate::i18n::Language::English {
            format!("{} {}", app.drive_name(), app.t("Space"))
        } else {
            format!("{}{}", app.drive_name(), app.t("Space"))
        };
        foreground = foreground
            .push(ring(
                disk.used as f64 / disk.total.max(1) as f64,
                &disk_label,
                ring_size,
            ))
            .push(
                column![
                    small(app.t("Available")).size(14),
                    bold(bytes(disk.free), if size.width < 850.0 { 22 } else { 26 })
                        .color(theme::MINT),
                    small(app.t("Total")).size(14),
                    label(bytes(disk.total)).size(18)
                ]
                .spacing(5)
                .width(capacity_width),
            );
    }
    foreground = foreground.push(Space::new().width(artwork_size));
    let asset = |name: &str| {
        svg(svg::Handle::from_memory(
            cplus_core::resource(&format!("assets/ui/{name}.svg")).unwrap_or_default(),
        ))
    };
    // Tiny-skia keeps an SVG's intrinsic aspect ratio when rasterizing. Give
    // the wave asset this card's viewport so it reaches the rounded bottom.
    let waves = String::from_utf8_lossy(
        cplus_core::resource("assets/ui/home-hero-waves.svg").unwrap_or_default(),
    )
    .replacen(
        "<svg ",
        &format!(
            "<svg width=\"{}\" height=\"{}\" ",
            (size.width - 2.0).round().max(1.0),
            hero_height - 2
        ),
        1,
    );
    let hero = container(
        stack![
            container(
                svg(svg::Handle::from_memory(waves.into_bytes()))
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .content_fit(iced::ContentFit::Fill)
            )
            .padding(1),
            container(
                asset("home-folder")
                    .width(artwork_size)
                    .height(artwork_size)
                    .opacity(0.7_f32)
            )
            .padding(iced::Padding {
                right: horizontal_padding,
                top: 14.0,
                ..Default::default()
            })
            .align_right(Length::Fill)
            .center_y(Length::Fill),
            container(foreground)
                .padding(iced::Padding {
                    left: horizontal_padding,
                    right: horizontal_padding,
                    top: if compact { 11.0 } else { 27.0 },
                    bottom: if compact { 11.0 } else { 27.0 },
                })
                .center_y(Length::Fill)
        ]
        .height(hero_height)
        .width(Length::Fill),
    )
    .style(theme::hero);

    let mut cards = vec![];
    for feature in Feature::HOME {
        let feedback: Element<'_, Message> = if app.busy() && app.progress.feature == feature {
            let p = &app.progress;
            let fraction = p
                .total
                .filter(|n| *n > 0)
                .map(|n| p.done as f32 / n as f32)
                .unwrap_or((p.elapsed_ms % 1800) as f32 / 1800.0)
                .min(1.0);
            row![
                progress_bar(0.0..=1.0, fraction)
                    .girth(5)
                    .style(theme::progress),
                small(app.t("Scanning")).size(11)
            ]
            .spacing(8)
            .align_y(iced::Alignment::Center)
            .into()
        } else if app.scan_elapsed.contains_key(&feature) {
            small(completion_text(app, feature)).size(11).into()
        } else {
            Space::new().height(14).into()
        };
        cards.push(
            container(
                column![
                    row![
                        nav_icon(feature, true, if compact { 24 } else { 30 }),
                        label(app.name(feature)).size(16)
                    ]
                    .spacing(if compact { 8 } else { 12 })
                    .align_y(iced::Alignment::Center),
                    row![
                        amount(app.metric(feature), 18),
                        Space::new().width(Length::Fill),
                        link(format!("{} ›", app.t("Open")), Message::Navigate(feature))
                    ]
                    .spacing(4)
                    .align_y(iced::Alignment::Center),
                    feedback
                ]
                .spacing(if compact { 5 } else { 8 }),
            )
            .padding(if compact { 8 } else { 14 })
            .height(if compact { 92 } else { 116 })
            .width(Length::Fill)
            .style(theme::card)
            .into(),
        );
    }
    column![
        hero,
        bold(app.t("Common tools"), if compact { 20 } else { 22 }),
        grid(cards, 4)
    ]
    .spacing(gap)
    .width(Length::Fill)
    .into()
}

fn completion_text(app: &App, feature: Feature) -> String {
    match app.scan_elapsed.get(&feature) {
        Some(elapsed) => format!(
            "{} · {:.1} {}",
            app.t("Scan complete"),
            *elapsed as f64 / 1000.0,
            app.t("seconds")
        ),
        None => app.t("Scan complete"),
    }
}
