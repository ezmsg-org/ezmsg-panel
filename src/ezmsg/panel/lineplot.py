import asyncio
from functools import partial
from typing import Dict, List, Optional

import ezmsg.core as ez
import numpy as np
import numpy.typing as npt
import panel
from bokeh.models import ColumnDataSource
from bokeh.models.renderers import GlyphRenderer
from bokeh.plotting import figure
from ezmsg.util.messages.axisarray import AxisArray
from param.parameterized import Event

from .util import AxisScale

CDS_X_DIM = "__x__"


class LinePlotSettings(ez.Settings):
    name: str = "LinePlot"
    x_axis: Optional[str] = None  # If not specified, dim 0 is used.
    x_axis_scale: AxisScale = AxisScale.LINEAR
    y_axis_scale: AxisScale = AxisScale.LINEAR
    y_axis_label: Optional[str] = None
    x_axis_label: Optional[str] = None


class LinePlotState(ez.State):
    x_data: npt.NDArray
    cds_data: Dict[str, npt.NDArray]

    # Visualization controls
    channelize: panel.widgets.Checkbox
    gain: panel.widgets.FloatInput

    update_ev: asyncio.Event
    cur_signal: Optional[AxisArray]


class LinePlot(ez.Unit):
    SETTINGS = LinePlotSettings
    STATE = LinePlotState

    INPUT_SIGNAL = ez.InputStream(Optional[AxisArray])

    def initialize(self) -> None:
        self.STATE.x_data = np.arange(0)
        self.STATE.cds_data = dict()

        self.STATE.update_ev = asyncio.Event()
        self.STATE.update_ev.clear()
        self.STATE.cur_signal = None

        self.STATE.channelize = panel.widgets.Checkbox(name="Channelize", value=True)
        self.STATE.gain = panel.widgets.FloatInput(name="Gain", value=1.0)

        def on_vis_control(*events: Event) -> None:
            self.STATE.update_ev.set()

        self.STATE.channelize.param.watch(on_vis_control, "value")
        self.STATE.gain.param.watch(on_vis_control, "value")

    def plot(self) -> panel.viewable.Viewable:
        cds = ColumnDataSource()

        x_axis_type, y_axis_type = "linear", "linear"

        if self.SETTINGS.x_axis_scale == AxisScale.LOG:
            x_axis_type = "log"
        if self.SETTINGS.y_axis_scale == AxisScale.LOG:
            y_axis_type = "log"

        axis_labels = dict()
        if self.SETTINGS.x_axis_label is not None:
            axis_labels["x_axis_label"] = self.SETTINGS.x_axis_label
        if self.SETTINGS.y_axis_label is not None:
            axis_labels["y_axis_label"] = self.SETTINGS.y_axis_label

        fig = figure(
            sizing_mode="stretch_width",
            title=self.SETTINGS.name,
            output_backend="webgl",
            x_axis_type=x_axis_type,
            y_axis_type=y_axis_type,
            tooltips=[("x", "$x"), ("y", "$y")],
            **axis_labels,
        )

        lines = dict()

        @panel.io.with_lock
        def _update(
            fig: figure, cds: ColumnDataSource, lines: Dict[str, GlyphRenderer]
        ) -> None:
            cds_data = {**self.STATE.cds_data, **{CDS_X_DIM: self.STATE.x_data}}

            for key in list(lines.keys() - self.STATE.cds_data.keys()):
                cds.remove(key)
                fig.renderers.remove(lines[key])
                del lines[key]

            for key in list(self.STATE.cds_data.keys() - lines.keys()):
                cds.add([], key)
                lines[key] = fig.line(x=CDS_X_DIM, y=key, source=cds)

            cds.data = cds_data

        _ = panel.state.add_periodic_callback(
            partial(_update, fig, cds, lines), period=50
        )

        return panel.pane.Bokeh(fig)

    @property
    def controls(self) -> List[panel.viewable.Viewable]:
        return [
            self.STATE.channelize,
            self.STATE.gain,
        ]

    def panel(self) -> panel.viewable.Viewable:
        return panel.Row(
            self.plot(), panel.Column("__Line Plot Controls__", *self.controls)
        )

    @ez.subscriber(INPUT_SIGNAL)
    async def on_signal(self, msg: Optional[AxisArray]) -> None:
        self.STATE.cur_signal = msg
        self.STATE.update_ev.set()

    @ez.task
    async def update_data(self) -> None:
        while True:
            await self.STATE.update_ev.wait()
            self.STATE.update_ev.clear()

            msg = self.STATE.cur_signal

            if msg is None:  # clear the plot
                self.STATE.x_data = np.arange(0)
                self.STATE.cds_data = dict()
                continue

            axis_name = self.SETTINGS.x_axis
            if axis_name is None:
                axis_name = msg.dims[0]
            axis = msg.get_axis(axis_name)

            with msg.view2d(axis_name) as view:
                ch_names = getattr(msg, "ch_names", None)
                if ch_names is None:
                    ch_names = [f"ch_{i}" for i in range(view.shape[1])]

                self.STATE.x_data = (np.arange(view.shape[0]) * axis.gain) + axis.offset
                vis_view = view * self.STATE.gain.value

                if self.STATE.channelize.value:
                    vis_view += np.arange(len(ch_names))

                self.STATE.cds_data = {
                    ch_name: vis_view[:, ch_idx]
                    for ch_idx, ch_name in enumerate(ch_names)
                }
