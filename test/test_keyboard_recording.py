"""Real-browser regressions for text committed by Enter before change/blur."""
import json

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome
from recorder_loader import recorder_source
from generate_spec import prepare_steps
from generate_trace import generate_trace
import trace_schema as ts


@pytest.fixture(scope='module')
def keyboard_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=resolve_chrome())
        yield browser
        browser.close()


@pytest.fixture
def keyboard_page(keyboard_browser):
    page = keyboard_browser.new_page()
    page.set_content('''<input id="search" placeholder="请输入终端名称">
        <input id="secret" type="password"><button id="other">Other</button>''')
    page.evaluate(recorder_source())
    yield page
    page.close()


def _input_steps(page):
    return page.evaluate("() => window.__rec.steps.filter(s => ['fill', 'press'].includes(s.type))")


def test_enter_records_current_text_before_press_without_waiting_for_blur(keyboard_page):
    field = keyboard_page.locator('#search')
    field.press_sequentially('11.26')
    field.press('Enter')

    steps = _input_steps(keyboard_page)
    assert [s['type'] for s in steps] == ['fill', 'press']
    assert steps[0]['value'] == '11.26'
    assert steps[1]['key'] == 'Enter'
    assert steps[0]['sel'] == steps[1]['sel']


def test_enter_preserves_text_when_submit_removes_input(keyboard_page):
    keyboard_page.evaluate("""() => document.querySelector('#search').addEventListener(
        'keydown', e => { if (e.key === 'Enter') e.target.remove(); })""")
    field = keyboard_page.locator('#search')
    field.fill('11.26')
    field.press('Enter')

    steps = _input_steps(keyboard_page)
    assert [s['type'] for s in steps] == ['fill', 'press']
    assert steps[0]['value'] == '11.26'


def test_blur_does_not_duplicate_enter_fill_and_later_edits_are_recorded(keyboard_page):
    field = keyboard_page.locator('#search')
    field.fill('11.26')
    field.press('Enter')
    keyboard_page.locator('#other').click()
    field.fill('11.27')
    field.press('Enter')
    keyboard_page.locator('#other').click()

    steps = _input_steps(keyboard_page)
    expected = [
        ('fill', '11.26'), ('press', 'Enter'),
        ('fill', '11.27'), ('press', 'Enter'),
    ]
    assert [(s['type'], s.get('value', s.get('key'))) for s in steps] == expected
    _, prepared = prepare_steps(steps)
    assert [(s['type'], s.get('value', s.get('key'))) for s in prepared] == expected
    trace = generate_trace(steps, [], name='keyboard-order', start_url='https://example.test/')
    assert [node['action']['type'] for _, node in ts.nodes(trace)] == [
        'InputText', 'PressKey', 'InputText', 'PressKey',
    ]


def test_password_enter_records_secret_marker_before_press(keyboard_page):
    field = keyboard_page.locator('#secret')
    field.fill('test-secret-not-for-artifacts')
    field.press('Enter')

    steps = _input_steps(keyboard_page)
    assert [s['type'] for s in steps] == ['fill', 'press']
    assert steps[0]['secret'] is True
    assert 'value' not in steps[0]
    assert 'test-secret-not-for-artifacts' not in json.dumps(steps)


def test_ime_confirmation_enter_is_not_recorded_as_submission(keyboard_page):
    keyboard_page.locator('#search').fill('终端')
    keyboard_page.locator('#search').dispatch_event(
        'keydown', {'key': 'Enter', 'isComposing': True},
    )
    assert _input_steps(keyboard_page) == []
    keyboard_page.locator('#search').press('Enter')
    assert [s['type'] for s in _input_steps(keyboard_page)] == ['fill', 'press']


def test_old_press_fill_recordings_still_normalize_input_before_enter():
    steps = [
        {'id':'old-press', 't':100, 'type':'press', 'sel':'locator("#search")', 'key':'Enter'},
        {'id':'old-fill', 't':200, 'type':'fill', 'sel':'locator("#search")', 'value':'11.26'},
    ]
    _, prepared = prepare_steps(steps)
    assert [s['type'] for s in prepared] == ['fill', 'press']
    assert prepared[0]['value'] == '11.26'
