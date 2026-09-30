from PySide6.QtCore import QEvent, QPointF, QSize
from PySide6.QtGui import QEnterEvent, QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QWidget

from ui_effects import PremiumButton


def test_button_hover_animates_and_keeps_click_signal() -> None:
    app = QApplication.instance() or QApplication([])
    app.setProperty("vpUiColors", {
        "field": "#142B43", "card_hover": "#1B3550", "border": "#29435F",
        "accent": "#67E8F9", "blue": "#2695F2", "text": "#EDF7FF",
        "muted": "#A1BED3", "danger": "#FB7185", "primary_text": "#061625",
        "soft": "#123E5A",
    })
    button = PremiumButton("Ordner auswählen")
    button.set_symbol("folder")
    button.resize(180, 44)
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    button.enterEvent(QEnterEvent(QPointF(17, 17), QPointF(17, 17), QPointF(17, 17)))
    QTest.qWait(240)
    assert button.hoverAmount > 0.95
    assert not button.grab().isNull()
    button.click()
    assert clicks == [True]
    button._animate(0.0)
    QTest.qWait(240)
    assert button.hoverAmount < 0.05


def test_focus_does_not_leave_close_button_in_hover_state() -> None:
    app = QApplication.instance() or QApplication([])
    button = PremiumButton("", objectName="CloseButton")
    button.set_symbol("close")
    button.resize(34, 34)
    button.show()
    button.setFocus()
    QTest.qWait(240)
    assert button.hoverAmount < 0.05
    button.enterEvent(QEnterEvent(QPointF(17, 17), QPointF(17, 17), QPointF(17, 17)))
    QTest.qWait(240)
    assert button.hoverAmount > 0.95
    button.leaveEvent(QEvent(QEvent.Leave))
    QTest.qWait(240)
    assert button.hoverAmount < 0.05
    button.close()


def test_refresh_hover_clears_after_animation() -> None:
    from gui import AnimatedRefreshButton

    app = QApplication.instance() or QApplication([])
    button = AnimatedRefreshButton(32, QSize(20, 20))
    button.show()
    button.enterEvent(QEnterEvent(QPointF(16, 16), QPointF(16, 16), QPointF(16, 16)))
    QTest.qWait(240)
    assert button.hoverAmount > 0.95
    button._finish_spin()
    QTest.qWait(240)
    assert button.hoverAmount < 0.05
    button.close()


def test_list_item_tooltip_uses_transparent_rounded_popup() -> None:
    from gui import TooltipManager

    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.dark = True
    manager = TooltipManager(owner)
    listing = QListWidget()
    item = QListWidgetItem("Song")
    item.setToolTip("Arrangeur: Test")
    listing.addItem(item)
    listing.show()
    app.processEvents()
    point = listing.visualItemRect(item).center()
    event = QHelpEvent(QEvent.ToolTip, point, listing.viewport().mapToGlobal(point))

    assert manager.eventFilter(listing.viewport(), event)
    assert manager.popup.isVisible()
    image = manager.popup.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    manager.popup.close()
    listing.close()


# -- by Larsiopuw
