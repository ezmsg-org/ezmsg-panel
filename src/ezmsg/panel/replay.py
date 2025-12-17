import asyncio
import time
import typing
from pathlib import Path

import ezmsg.core as ez
import panel
from ezmsg.util.messagereplay import (
    FileReplayMessage,
    MessageReplay,
    ReplayStatusMessage,
)
from param.parameterized import Event

from .tabbedapp import Tab


class ReplaySettings(ez.Settings):
    data_dir: Path
    name: str = "Message Replay"
    msg_rate_window = 2.0  # sec


class ReplayGUIState(ez.State):
    # Diagnostic Widgets
    message_rate: panel.widgets.Number

    # Playback Controls
    file_selector: panel.widgets.FileSelector
    enqueue_button: panel.widgets.Button
    pause_toggle: panel.widgets.Toggle
    stop_button: panel.widgets.Button
    playback_file: panel.widgets.StaticText
    playback: panel.indicators.Progress

    rapid: panel.widgets.Checkbox
    rate: panel.widgets.FloatInput

    # Support
    file_queue: "asyncio.Queue[Path]"
    stop_queue: "asyncio.Queue[bool]"
    pause_queue: "asyncio.Queue[bool]"
    msg_times: typing.List[float]
    replay_status: typing.Optional[ReplayStatusMessage] = None


class ReplayGUI(ez.Unit):
    SETTINGS = ReplaySettings
    STATE = ReplayGUIState

    INPUT_REPLAY_STATUS = ez.InputStream(ReplayStatusMessage)
    OUTPUT_FILE_REPLAY = ez.OutputStream(FileReplayMessage)

    OUTPUT_STOP = ez.OutputStream(bool)
    OUTPUT_PAUSE = ez.OutputStream(bool)

    async def initialize(self) -> None:
        self.STATE.file_queue = asyncio.Queue()
        self.STATE.stop_queue = asyncio.Queue()
        self.STATE.pause_queue = asyncio.Queue()

        self.STATE.rapid = panel.widgets.Checkbox(name="Rapid", value=True)
        self.STATE.rate = panel.widgets.FloatInput(
            name='Playback Rate (Hz, 0.0 = "as recorded")',
            value=0.0,
            start=0.0,
            disabled=True,
        )

        self.STATE.rapid.link(self.STATE.rate, value="disabled")

        self.SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
        self.STATE.file_selector = panel.widgets.FileSelector(self.SETTINGS.data_dir)
        self.STATE.enqueue_button = panel.widgets.Button(
            name="Replay Selected", width=200
        )

        def enqueue(*events: Event) -> None:
            for fpath in self.STATE.file_selector.value:
                rate = None if self.STATE.rapid.value else self.STATE.rate.value
                msg = FileReplayMessage(filename=Path(fpath), rate=rate)
                self.STATE.file_queue.put_nowait(msg)

        self.STATE.enqueue_button.on_click(enqueue)

        self.STATE.stop_button = panel.widgets.Button(name="⏹️", width=50)

        def stop(*events: Event) -> None:
            self.STATE.stop_queue.put_nowait(True)

        self.STATE.stop_button.on_click(stop)

        self.STATE.pause_toggle = panel.widgets.Toggle(name="⏸️", width=50)

        def pause(*events: Event) -> None:
            self.STATE.pause_queue.put_nowait(self.STATE.pause_toggle.value)
            self.STATE.playback.loading = self.STATE.pause_toggle.value

        self.STATE.pause_toggle.param.watch(pause, "value")

        self.STATE.playback_file = panel.widgets.StaticText(name="Replaying", value="-")

        self.STATE.playback = panel.indicators.Progress(
            value=100, max=100, bar_color="success", sizing_mode="stretch_width"
        )

        number_kwargs = dict(title_size="12pt", font_size="18pt")

        self.STATE.message_rate = panel.widgets.Number(
            name="Current Replay Message Rate", format="{value} Hz", **number_kwargs
        )

        self.STATE.msg_times = list()

    def content(self) -> panel.viewable.Viewable:
        return self.STATE.file_selector

    def controls(self) -> panel.viewable.Viewable:
        return panel.Column(
            self.STATE.message_rate,
            self.STATE.rate,
            self.STATE.rapid,
            panel.Row(
                self.STATE.enqueue_button,
                self.STATE.pause_toggle,
                self.STATE.stop_button,
            ),
            self.STATE.playback_file,
            self.STATE.playback,
        )

    def panel(self) -> panel.viewable.Viewable:
        return panel.Row(self.content, self.controls)

    @ez.publisher(OUTPUT_FILE_REPLAY)
    async def start_file(self) -> typing.AsyncGenerator:
        while True:
            file_replay_msg = await self.STATE.file_queue.get()
            yield self.OUTPUT_FILE_REPLAY, file_replay_msg

    @ez.publisher(OUTPUT_STOP)
    async def stop_file(self) -> typing.AsyncGenerator:
        while True:
            val = await self.STATE.stop_queue.get()
            yield self.OUTPUT_STOP, val

    @ez.publisher(OUTPUT_PAUSE)
    async def pause(self) -> typing.AsyncGenerator:
        while True:
            val = await self.STATE.pause_queue.get()
            yield self.OUTPUT_PAUSE, val

    @ez.subscriber(INPUT_REPLAY_STATUS)
    async def on_replay_status(self, msg: ReplayStatusMessage) -> None:
        now = time.time()
        self.STATE.msg_times.append(now)
        self.STATE.replay_status = msg

    @ez.task
    async def update_display(self) -> None:
        t_window = self.SETTINGS.msg_rate_window
        while True:
            await asyncio.sleep(0.2)
            cur_time = time.time()
            self.STATE.msg_times = [
                t for t in self.STATE.msg_times if (cur_time - t) < t_window
            ]
            self.STATE.message_rate.value = len(self.STATE.msg_times) / t_window

            if self.STATE.replay_status is not None:
                playback_pct = (
                    self.STATE.replay_status.idx / self.STATE.replay_status.total
                )
                self.STATE.playback_file.value = str(
                    self.STATE.replay_status.filename.name
                )
                self.STATE.playback.value = int(100 * playback_pct)
                self.STATE.playback.bar_color = (
                    "success" if self.STATE.replay_status.done else "primary"
                )


class Replay(ez.Collection, Tab):
    SETTINGS = ReplaySettings

    OUTPUT_MESSAGE = ez.InputStream(typing.Any)
    OUTPUT_REPLAY_STATUS = ez.OutputStream(ReplayStatusMessage)

    GUI = ReplayGUI()
    REPLAY = MessageReplay()

    @property
    def title(self) -> str:
        return self.SETTINGS.name

    def content(self) -> panel.viewable.Viewable:
        return self.GUI.content()

    def sidebar(self) -> panel.viewable.Viewable:
        return self.GUI.controls()

    def configure(self) -> None:
        self.GUI.apply_settings(self.SETTINGS)

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.REPLAY.OUTPUT_MESSAGE, self.OUTPUT_MESSAGE),
            (self.REPLAY.OUTPUT_REPLAY_STATUS, self.OUTPUT_REPLAY_STATUS),
            (self.GUI.OUTPUT_FILE_REPLAY, self.REPLAY.INPUT_FILE),
            (self.GUI.OUTPUT_STOP, self.REPLAY.INPUT_STOP),
            (self.GUI.OUTPUT_PAUSE, self.REPLAY.INPUT_PAUSED),
            (self.REPLAY.OUTPUT_REPLAY_STATUS, self.GUI.INPUT_REPLAY_STATUS),
        )

    def process_components(self) -> typing.Tuple[ez.Component, ...]:
        return (self.REPLAY,)

    def panel(self) -> panel.viewable.Viewable:
        return self.GUI.panel()
