use super::{theme, widgets::label};
use crate::app::{App, Dialog, Message};
use cplus_core::Feature;
use iced::{
    Element, Length, Size,
    widget::{Column, button, container, rule},
};

pub const SIZE: Size = Size::new(236.0, 262.0);

pub fn view(app: &App) -> Element<'_, Message> {
    let item = |name: String, action: Message| {
        button(label(name).size(13))
            .width(Length::Fill)
            .height(36)
            .padding([9, 14])
            .style(theme::menu_item)
            .on_press(Message::TrayAction(Box::new(action)))
    };
    let mut menu = Column::new().spacing(2);
    for feature in [
        Feature::Drive,
        Feature::Chat,
        Feature::Privacy,
        Feature::Popup,
    ] {
        menu = menu.push(item(app.name(feature), Message::Navigate(feature)));
    }
    let separator = || {
        container(rule::horizontal(1).style(|_| iced::widget::rule::Style {
            color: theme::LINE,
            radius: 0.0.into(),
            fill_mode: iced::widget::rule::FillMode::Full,
            snap: true,
        }))
        .padding([3, 0])
    };
    menu = menu
        .push(separator())
        .push(item(app.t("Settings"), Message::Dialog(Dialog::Settings)))
        .push(separator())
        .push(item(app.t("Exit"), Message::Exit));
    container(menu)
        .padding(8)
        .width(Length::Fill)
        .height(Length::Fill)
        .style(|_| container::Style {
            background: Some(iced::Color::WHITE.into()),
            border: iced::Border {
                color: theme::LINE,
                width: 1.0,
                radius: 8.0.into(),
            },
            ..Default::default()
        })
        .into()
}
