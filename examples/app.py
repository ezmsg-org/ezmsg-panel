import asyncio
from typing import AsyncGenerator, Tuple

import ezmsg.core as ez
import panel
from panel.viewable import Viewable
from param.parameterized import Event

from ezmsg.panel.application import Application, ApplicationSettings


class NumberPanelSettings(ez.Settings):
    default_value: float = 1.0


class NumberPanelState(ez.State):
    number: panel.widgets.FloatInput
    number_queue: "asyncio.Queue[float]"


class NumberPanel(ez.Unit):
    SETTINGS = NumberPanelSettings
    STATE = NumberPanelState

    OUTPUT_NUMBER = ez.OutputStream(float)

    def initialize(self) -> None:
        self.STATE.number = panel.widgets.FloatInput(
            name="Number", value=self.SETTINGS.default_value, start=0, end=10.0
        )

        # It's important to create asyncio synchronization primitives
        # in initialize(), or using field(default_factory = ...) so that
        # they're linked with the correct asyncio event loop
        self.STATE.number_queue = asyncio.Queue()

        def on_number_changed(*events: Event):
            # Could also grab new value from event in callback
            # https://panel.holoviz.org/user_guide/Links.html#event-objects
            self.STATE.number_queue.put_nowait(self.STATE.number.value)

        self.STATE.number.param.watch(on_number_changed, "value")

    @ez.publisher(OUTPUT_NUMBER)
    async def publish_number(self) -> AsyncGenerator:
        while True:
            number = await self.STATE.number_queue.get()
            yield self.OUTPUT_NUMBER, number

    def panel(self) -> Viewable:
        return panel.Column("# Pick a number", self.STATE.number)


class OutputPanelState(ez.State):
    number: panel.widgets.Number


class OutputPanel(ez.Unit):
    STATE = OutputPanelState

    INPUT_NUMBER = ez.InputStream(float)

    def initialize(self) -> None:
        self.STATE.number = panel.widgets.Number(name="Most Recent Number")

    @ez.subscriber(INPUT_NUMBER)
    async def on_number_change(self, msg: float) -> None:
        self.STATE.number.value = msg

    def panel(self) -> Viewable:
        return panel.Column("# HELLO WORLD!", self.STATE.number)


class PanelExample(ez.Collection):
    SETTINGS = ApplicationSettings

    APP = Application()
    NUMBER_OUTPUT = OutputPanel()
    NUMBER_PANEL = NumberPanel()

    def configure(self) -> None:
        self.APP.apply_settings(self.SETTINGS)

        self.APP.panels = {
            "Select": self.NUMBER_PANEL.panel,
            "Output": self.NUMBER_OUTPUT.panel,
        }

    def network(self) -> ez.NetworkDefinition:
        return ((self.NUMBER_PANEL.OUTPUT_NUMBER, self.NUMBER_OUTPUT.INPUT_NUMBER),)

    # IMPORTANT: ALL viewable panels must exist in SAME process
    # as the Application.  If you need to split out computation
    # to another process, de-couple your UI/Panel unit from the compute unit.
    def process_components(self) -> Tuple[ez.Component, ...]:
        return (
            # self.NUMBER_OUTPUT, # Uncomment me and this panel doesn't work anymore!
        )


if __name__ == "__main__":
    settings = ApplicationSettings(port=8080, name="Example Panel for ezmsg")

    app = PanelExample(settings)

    ez.logger.info("Startup can take a while the first time this starts")
    ez.run(APP=app)
