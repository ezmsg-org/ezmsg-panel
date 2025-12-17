import math
import time
import typing

import ezmsg.core as ez
import panel as pn
from bokeh.model import Model
from bokeh.models import ColumnDataSource
from bokeh.plotting import figure
from ezmsg.util.rate import Rate

from ezmsg.panel.application import Application, ApplicationSettings

# Dealing with widgets in panel is easy and straight-forward!
# Unfortunately, streaming data to/updating Bokeh figures is NOT.
# Bokeh figures need to be created/updated per-session, which
# causes some unfortunate headaches. This example is here to
# demonstrate a scalable pattern for live Bokeh plots in ezmsg/panel


class SimpleFigure:
    fig: Model
    cds: Model
    new_data: typing.List[typing.Tuple[float, float]]
    rollover: int

    def __init__(self, rollover: int) -> None:
        self.fig = figure()
        self.cds = ColumnDataSource(data={"x": [], "y": []})
        self.fig.line(x="x", y="y", source=self.cds)

        self.new_data = []
        self.rollover = rollover

    def add_point(self, x: float, y: float) -> None:
        self.new_data.append((x, y))

    @pn.io.with_lock
    async def update(self):
        if len(self.new_data):
            x, y = zip(*self.new_data)
            self.cds.stream({"x": x, "y": y}, rollover=self.rollover)
        self.new_data.clear()


class BokehExampleSettings(ez.Settings):
    rollover: int = 100


class BokehExampleState(ez.State):
    cur_x: float = 0.0
    figures: typing.Set[SimpleFigure]


class BokehExample(ez.Unit):
    SETTINGS = BokehExampleSettings
    STATE = BokehExampleState

    INPUT = ez.InputStream(float)

    async def initialize(self):
        self.STATE.figures = set()

    @ez.subscriber(INPUT)
    async def on_number(self, num: float) -> None:
        """Called every time there's a new data point for our figure"""

        # Here, we manually update every client with new data
        ez.logger.debug(f"Updating {len(self.STATE.figures)} figures")
        for figure_ in self.STATE.figures:
            figure_.add_point(self.STATE.cur_x, num)
        self.STATE.cur_x += 1.0

    def panel(self) -> pn.viewable.Viewable:
        """This method is called once for each client that requests this panel"""

        # We have to create Bokeh models per-client and keep track of them individually
        # This is the only way we can update/stream content to/from these models
        plot = SimpleFigure(self.SETTINGS.rollover)
        pn.state.add_periodic_callback(plot.update, period=100)  # ms
        self.STATE.figures.add(plot)
        pn.state.on_session_destroyed(lambda _: self.STATE.figures.remove(plot))

        return pn.Column(
            "# Bokeh Example",
            plot.fig,
        )


class NumberPublisherSettings(ez.Settings):
    pub_rate: float = 2.0


class NumberPublisher(ez.Unit):
    SETTINGS = NumberPublisherSettings

    OUTPUT_NUMBER = ez.OutputStream(float)

    @ez.publisher(OUTPUT_NUMBER)
    async def pub_number(self) -> typing.AsyncGenerator:
        rate = Rate(self.SETTINGS.pub_rate)
        while True:
            await rate.sleep()
            yield self.OUTPUT_NUMBER, math.sin(time.time())


if __name__ == "__main__":
    numbers = NumberPublisher()
    example = BokehExample()

    app = Application(ApplicationSettings(port=0, name="Bokeh Example"))

    app.panels = {"example": example.panel}

    ez.run(
        NUMBERS=numbers,
        EXAMPLE=example,
        APP=app,
        connections=((numbers.OUTPUT_NUMBER, example.INPUT),),
    )
