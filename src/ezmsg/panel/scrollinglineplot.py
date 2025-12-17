import asyncio
import typing
from functools import partial
from typing import Dict, Optional, Set

import ezmsg.core as ez
import numpy as np
import panel
from bokeh.models import ColumnDataSource
from bokeh.models.renderers import GlyphRenderer
from bokeh.plotting import figure
from bokeh.server.contexts import BokehSessionContext
from ezmsg.util.messages.axisarray import AxisArray, LinearAxis
from param.parameterized import Event

from .tabbedapp import Tab

CDS_TIME_DIM = "__time__"


class ScrollingLinePlotSettings(ez.Settings):
    name: str = "Scrolling Line Plot"
    time_axis: Optional[str] = None  # If not specified, dim 0 is used.
    initial_gain: float = 1.0
    downsample_factor: int = 1


class ScrollingLinePlotState(ez.State):
    queues: Set["asyncio.Queue[Dict[str, np.ndarray]]"]
    cur_t: float = 0.0
    cur_fs: float = 1.0

    # Visualization controls
    channelize: panel.widgets.Checkbox
    gain: panel.widgets.FloatInput
    duration: panel.widgets.FloatInput
    downsample: panel.widgets.IntInput

    # Signal Properties
    fs: panel.widgets.Number
    n_time: panel.widgets.Number

    downsampler: typing.Generator[AxisArray, AxisArray, None]


class ScrollingLinePlot(ez.Unit, Tab):
    SETTINGS = ScrollingLinePlotSettings
    STATE = ScrollingLinePlotState

    INPUT_SIGNAL = ez.InputStream(AxisArray)

    async def initialize(self) -> None:
        self.STATE.queues = set()
        self.STATE.channelize = panel.widgets.Checkbox(name="Channelize", value=True)
        self.STATE.gain = panel.widgets.FloatInput(
            name="Gain", value=self.SETTINGS.initial_gain, sizing_mode="stretch_width"
        )
        self.STATE.duration = panel.widgets.FloatInput(
            name="Duration (sec)", value=4.0, start=0.0, sizing_mode="stretch_width"
        )
        self.STATE.downsample = panel.widgets.IntInput(
            name="Downsample", value=0, start=0, step=1, sizing_mode="stretch_width"
        )

        def update_downsampling(e: Event) -> None:
            try:
                from ezmsg.sigproc.downsample import downsample

                self.STATE.downsampler = downsample(
                    axis=self.SETTINGS.time_axis, factor=1 if e.new <= 0 else e.new
                )
            except ImportError:
                ez.logger.warning(
                    "could not import ezmsg-sigproc. downsample not functional"
                )

        self.STATE.downsample.param.watch(update_downsampling, "value")
        self.STATE.downsample.value = (
            self.SETTINGS.downsample_factor
        )  # Force update of downsampler

        number_kwargs = dict(title_size="12pt", font_size="18pt")
        self.STATE.fs = panel.widgets.Number(
            name="Sampling Rate", format="{value} Hz", **number_kwargs
        )
        self.STATE.n_time = panel.widgets.Number(
            name="Samples per Message", **number_kwargs
        )

    def plot(self) -> panel.viewable.Viewable:
        queue: "asyncio.Queue[Dict[str, np.ndarray]]" = asyncio.Queue()
        cds = ColumnDataSource({CDS_TIME_DIM: [self.STATE.cur_t]})
        fig = figure(
            sizing_mode="stretch_width",
            title=self.SETTINGS.name,
            output_backend="webgl",
            tooltips=[("x", "$x"), ("y", "$y")],
        )

        lines = {}

        @panel.io.with_lock
        async def _update(
            fig: figure,
            cds: ColumnDataSource,
            queue: "asyncio.Queue[ Dict[ str, np.ndarray ] ]",
            lines: Dict[str, GlyphRenderer],
        ) -> None:
            while not queue.empty():
                cds_data: Dict[str, np.ndarray] = queue.get_nowait()

                ch_names = [ch for ch in cds_data.keys() if ch != CDS_TIME_DIM]
                offsets = (
                    np.arange(len(ch_names))
                    if self.STATE.channelize.value
                    else np.zeros(len(ch_names))
                )

                cds_data = {
                    ch: (arr * self.STATE.gain.value) + offsets[ch_names.index(ch)]
                    if ch != CDS_TIME_DIM
                    else arr
                    for ch, arr in cds_data.items()
                }

                # Add new lines to plot as necessary
                # TODO: Remove lines from plot as necessary
                for key, arr in cds_data.items():
                    if key not in cds.column_names:
                        cds.add([arr[0]], key)
                        lines[key] = fig.line(x=CDS_TIME_DIM, y=key, source=cds)

                cds.stream(
                    cds_data,
                    rollover=int(self.STATE.duration.value * self.STATE.cur_fs),
                )

        _ = panel.state.add_periodic_callback(
            partial(_update, fig, cds, queue, lines), period=50
        )

        def remove_queue(_: BokehSessionContext) -> None:
            self.STATE.queues.remove(queue)

        self.STATE.queues.add(queue)
        panel.state.on_session_destroyed(remove_queue)

        return panel.pane.Bokeh(fig)

    @property
    def title(self) -> str:
        return self.SETTINGS.name

    def content(self) -> panel.viewable.Viewable:
        return panel.Card(
            self.plot(),
            hide_header=True,
            sizing_mode="stretch_both",
        )

    def sidebar(self) -> panel.viewable.Viewable:
        return panel.Card(
            self.STATE.fs,
            self.STATE.n_time,
            self.STATE.channelize,
            panel.Row(self.STATE.gain, self.STATE.duration, self.STATE.downsample),
            title="Scrolling Line Plot Controls",
            collapsed=True,
            sizing_mode="stretch_width",
        )

    def panel(self) -> panel.viewable.Viewable:
        return panel.Row(self.plot(), self.sidebar())

    @ez.subscriber(INPUT_SIGNAL)
    async def on_signal(self, msg: AxisArray) -> None:
        axis_name = self.SETTINGS.time_axis
        if axis_name is None:
            axis_name = msg.dims[0]
        axis_info = msg.ax(axis_name)
        axis = axis_info.axis
        assert isinstance(
            axis, LinearAxis
        ), "ScrollingLinePlot only compatible with time_axis as LinearAxis"

        fs = 1.0 / axis.gain
        self.STATE.fs.value = fs
        self.STATE.n_time.value = axis_info.size

        # Downsample for visualization
        msg = self.STATE.downsampler.send(msg)
        q: int = self.STATE.downsample.value  # type: ignore
        fs /= q

        with msg.view2d(axis_name) as view:
            ch_names = getattr(msg, "ch_names", None)
            if ch_names is None:
                ch_names = [f"ch_{i}" for i in range(view.shape[1])]

            t = (np.arange(view.shape[0]) / fs) + self.STATE.cur_t
            cds_data = {CDS_TIME_DIM: t}
            for ch_idx, ch_name in enumerate(ch_names):
                cds_data[ch_name] = view[:, ch_idx]

            self.STATE.cur_fs = fs
            self.STATE.cur_t += view.shape[0] / fs

            for queue in self.STATE.queues:
                queue.put_nowait(cds_data)
