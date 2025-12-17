import asyncio
from dataclasses import dataclass, replace
from typing import AsyncGenerator

import ezmsg.core as ez
import panel
from ezmsg.util.messagequeue import MessageQueue, MessageQueueSettings
from ezmsg.util.messages.axisarray import AxisArray
from param.parameterized import Event

from .scrollinglineplot import (
    ScrollingLinePlot,
    ScrollingLinePlotSettings,
)
from .tabbedapp import Tab

try:
    from ezmsg.sigproc.butterworthfilter import (
        ButterworthFilter,
        ButterworthFilterSettings,
    )

except ImportError:
    ButterworthFilter = None

    @dataclass
    class ButterworthFilterSettings: ...


class ButterworthFilterControlState(ez.State):
    queue: asyncio.Queue[ButterworthFilterSettings]

    # Controls for Butterworth Filter
    order: panel.widgets.IntInput
    cuton: panel.widgets.FloatInput
    cutoff: panel.widgets.FloatInput


class ButterworthFilterControl(ez.Unit):
    SETTINGS = ButterworthFilterSettings
    STATE = ButterworthFilterControlState

    OUTPUT_SETTINGS = ez.OutputStream(ButterworthFilterSettings)

    async def initialize(self) -> None:
        self.STATE.queue = asyncio.Queue()

        # Spectrum Settings
        self.STATE.order = panel.widgets.IntInput(
            name='Filter Order (0 = "Disabled")', value=0, start=0
        )

        self.STATE.cuton = panel.widgets.FloatInput(
            name="Filter Cuton (Hz)", value=1.0, start=0.0
        )

        self.STATE.cutoff = panel.widgets.FloatInput(
            name="Filter Cutoff (Hz)", value=30.0, start=0.0
        )

        def enqueue_design(*events: Event) -> None:
            self.STATE.queue.put_nowait(
                replace(
                    self.SETTINGS,
                    order=self.STATE.order.value,
                    cuton=self.STATE.cuton.value,
                    cutoff=self.STATE.cutoff.value,
                )
            )

        self.STATE.order.param.watch(enqueue_design, "value")
        self.STATE.cuton.param.watch(enqueue_design, "value")
        self.STATE.cutoff.param.watch(enqueue_design, "value")

    @ez.publisher(OUTPUT_SETTINGS)
    async def pub_settings(self) -> AsyncGenerator:
        while True:
            settings = await self.STATE.queue.get()
            yield self.OUTPUT_SETTINGS, settings

    def controls(self) -> panel.viewable.Viewable:
        return panel.Card(
            self.STATE.order,
            self.STATE.cuton,
            self.STATE.cutoff,
            title="Butterworth Filter Controls",
            collapsed=True,
            sizing_mode="stretch_width",
        )


TimeSeriesPlotSettings = ScrollingLinePlotSettings


class TimeSeriesPlot(ez.Collection, Tab):
    SETTINGS = TimeSeriesPlotSettings

    INPUT_SIGNAL = ez.InputStream(AxisArray)

    BPFILT = None
    BPFILT_CONTROL = None
    if ButterworthFilter is not None:
        BPFILT = ButterworthFilter()
        BPFILT_CONTROL = ButterworthFilterControl()

    QUEUE = MessageQueue(MessageQueueSettings(maxsize=10, leaky=True))
    SCROLLING_PLOT = ScrollingLinePlot()

    @property
    def title(self) -> str:
        return self.SETTINGS.name

    def content(self) -> panel.viewable.Viewable:
        return self.SCROLLING_PLOT.content()

    def sidebar(self) -> panel.viewable.Viewable:
        bar = panel.Column(self.SCROLLING_PLOT.sidebar())
        if self.BPFILT_CONTROL is not None:
            bar.append(self.BPFILT_CONTROL.controls())
        return bar

    def configure(self) -> None:
        self.SCROLLING_PLOT.apply_settings(self.SETTINGS)

        if self.BPFILT is not None and self.BPFILT_CONTROL is not None:
            filter_settings = ButterworthFilterSettings(axis=self.SETTINGS.time_axis)

            self.BPFILT_CONTROL.apply_settings(filter_settings)
            self.BPFILT.apply_settings(filter_settings)

    def network(self) -> ez.NetworkDefinition:
        if self.BPFILT is not None and self.BPFILT_CONTROL is not None:
            return (
                (self.BPFILT_CONTROL.OUTPUT_SETTINGS, self.BPFILT.INPUT_FILTER),
                (self.INPUT_SIGNAL, self.BPFILT.INPUT_SIGNAL),
                (self.BPFILT.OUTPUT_SIGNAL, self.QUEUE.INPUT),
                (self.QUEUE.OUTPUT, self.SCROLLING_PLOT.INPUT_SIGNAL),
            )
        else:
            return (
                (self.INPUT_SIGNAL, self.QUEUE.INPUT),
                (self.QUEUE.OUTPUT, self.SCROLLING_PLOT.INPUT_SIGNAL),
            )

    def panel(self) -> panel.viewable.Viewable:
        return panel.Row(self.content(), self.sidebar())
