import asyncio
import time
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional, Tuple

import ezmsg.core as ez
import panel
from ezmsg.util.messagelogger import MessageLogger, MessageLoggerSettings
from param.parameterized import Event

from .tabbedapp import Tab


class RecorderSettings(ez.Settings):
    data_dir: Path
    name: str = "Message Recorder"
    msg_rate_window: float = 2.0  # sec
    write_period: float = 0.0  # sec


class RecorderGUIState(ez.State):
    # Diagnostic Widgets
    message_rate: panel.widgets.Number

    # Recording Controls
    file_selector: panel.widgets.FileSelector
    rec_dir: panel.widgets.TextInput
    rec_name: panel.widgets.TextInput
    rec_button: panel.widgets.Button
    stop_button: panel.widgets.Button
    rec_file: panel.widgets.StaticText
    rec_msgs: panel.widgets.Number

    # Support
    msg_times: List[float]
    cur_rec: Optional[Path] = None
    start_queue: "asyncio.Queue[Path]"
    stop_queue: "asyncio.Queue[Path]"
    n_msgs: int = 0


class RecorderGUI(ez.Unit):
    SETTINGS = RecorderSettings
    STATE = RecorderGUIState

    INPUT_MESSAGE = ez.InputStream(Any)

    OUTPUT_START = ez.OutputStream(Path)
    INPUT_START = ez.InputStream(Path)
    OUTPUT_STOP = ez.OutputStream(Path)
    INPUT_STOP = ez.InputStream(Path)

    async def initialize(self) -> None:
        self.STATE.start_queue = asyncio.Queue()
        self.STATE.stop_queue = asyncio.Queue()

        self.SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
        self.STATE.file_selector = panel.widgets.FileSelector(self.SETTINGS.data_dir)

        self.STATE.rec_dir = panel.widgets.TextInput(name="Recording Subdirectory")
        self.STATE.rec_name = panel.widgets.TextInput(name="Recording Name")
        self.STATE.rec_button = panel.widgets.Button(name="Start", width=50)

        def start_rec(*events: Event) -> None:
            self.STATE.rec_button.disabled = True
            rec_dir = self.STATE.rec_dir.value
            rec_name = self.STATE.rec_name.value
            rec_path = self.SETTINGS.data_dir
            rec_path = rec_path / rec_dir if rec_dir else rec_path
            out_fname = time.strftime("%Y%m%dT%H%M%S")
            out_fname = f"{rec_name}_{out_fname}" if rec_name else out_fname
            rec_path = rec_path / f"{out_fname}.txt"
            self.STATE.start_queue.put_nowait(rec_path)

        self.STATE.rec_button.on_click(start_rec)

        self.STATE.stop_button = panel.widgets.Button(name="Stop", width=50)
        self.STATE.stop_button.disabled = True

        def stop_rec(*events: Event) -> None:
            self.STATE.stop_button.disabled = True
            self.STATE.stop_queue.put_nowait(self.STATE.cur_rec)

        self.STATE.stop_button.on_click(stop_rec)

        self.STATE.rec_file = panel.widgets.StaticText(name="Recording", value="-")

        number_kwargs = dict(title_size="12pt", font_size="18pt")

        self.STATE.message_rate = panel.widgets.Number(
            name="Incoming Message Rate", format="{value} Hz", **number_kwargs
        )

        self.STATE.rec_msgs = panel.widgets.Number(
            format="{value} msgs", value=0, **number_kwargs
        )

        self.STATE.msg_times = list()

    def controls(self) -> panel.viewable.Viewable:
        return panel.Column(
            self.STATE.message_rate,
            self.STATE.rec_dir,
            self.STATE.rec_name,
            panel.Row(
                self.STATE.rec_button,
                self.STATE.stop_button,
            ),
            self.STATE.rec_file,
            self.STATE.rec_msgs,
        )

    def content(self) -> panel.viewable.Viewable:
        return self.STATE.file_selector

    def panel(self) -> panel.viewable.Viewable:
        return panel.Row(self.content(), self.controls())

    @ez.publisher(OUTPUT_START)
    async def start_file(self) -> AsyncGenerator:
        while True:
            rec_path = await self.STATE.start_queue.get()
            yield self.OUTPUT_START, rec_path

    @ez.subscriber(INPUT_START)
    async def on_file_start(self, msg: Path) -> None:
        self.STATE.cur_rec = msg
        self.STATE.n_msgs = 0
        self.STATE.rec_file.value = str(msg.parent / msg.name)
        self.STATE.rec_file.loading = True
        self.STATE.stop_button.disabled = False

    @ez.publisher(OUTPUT_STOP)
    async def stop_file(self) -> AsyncGenerator:
        while True:
            rec_path = await self.STATE.stop_queue.get()
            yield self.OUTPUT_STOP, rec_path

    @ez.subscriber(INPUT_STOP)
    async def on_file_stop(self, msg: Path) -> None:
        self.STATE.rec_button.disabled = False
        self.STATE.cur_rec = None
        self.STATE.rec_file.loading = False
        self.STATE.file_selector._refresh()

    @ez.task
    async def update_display(self) -> None:
        t_window = self.SETTINGS.msg_rate_window
        while True:
            await asyncio.sleep(1.0)
            cur_time = time.time()
            self.STATE.msg_times = [
                t for t in self.STATE.msg_times if (cur_time - t) < t_window
            ]
            self.STATE.message_rate.value = len(self.STATE.msg_times) / t_window
            self.STATE.rec_msgs.value = self.STATE.n_msgs

    @ez.subscriber(INPUT_MESSAGE)
    async def on_signal(self, msg: Any) -> None:
        now = time.time()
        self.STATE.msg_times.append(now)

        if self.STATE.cur_rec is not None:
            self.STATE.n_msgs += 1


class Recorder(ez.Collection, Tab):
    SETTINGS = RecorderSettings

    INPUT_MESSAGE = ez.InputStream(Any)

    GUI = RecorderGUI()
    LOGGER = MessageLogger()

    @property
    def title(self) -> str:
        return self.SETTINGS.name

    def content(self) -> panel.viewable.Viewable:
        return self.GUI.content()

    def sidebar(self) -> panel.viewable.Viewable:
        return self.GUI.controls()

    def configure(self) -> None:
        self.GUI.apply_settings(self.SETTINGS)

        if self.SETTINGS.write_period > 0:
            self.LOGGER.apply_settings(
                MessageLoggerSettings(
                    write_period=self.SETTINGS.write_period,
                )
            )

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.INPUT_MESSAGE, self.GUI.INPUT_MESSAGE),
            (self.INPUT_MESSAGE, self.LOGGER.INPUT_MESSAGE),
            (self.GUI.OUTPUT_START, self.LOGGER.INPUT_START),
            (self.LOGGER.OUTPUT_START, self.GUI.INPUT_START),
            (self.GUI.OUTPUT_STOP, self.LOGGER.INPUT_STOP),
            (self.LOGGER.OUTPUT_STOP, self.GUI.INPUT_STOP),
        )

    def process_components(self) -> Tuple[ez.Component, ...]:
        return (self.LOGGER,)

    def panel(self) -> panel.viewable.Viewable:
        return self.GUI.panel()
