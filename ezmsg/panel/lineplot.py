from functools import partial
from dataclasses import dataclass

import panel
import ezmsg.core as ez
import numpy as np
import numpy.typing as npt

from ezmsg.util.messages.axisarray import AxisArray

from bokeh.plotting import figure, Figure
from bokeh.models import ColumnDataSource
from bokeh.models.renderers import GlyphRenderer

from typing import Dict, Optional, List

CDS_X_DIM = '__x__'

@dataclass
class LinePlotSettingsMessage:
    name: str = 'LinePlot'
    x_axis: Optional[str] = None # If not specified, dim 0 is used.

class LinePlotSettings(ez.Settings, LinePlotSettingsMessage):
    ...

class LinePlotState( ez.State ):
    x_data: npt.NDArray
    cds_data: Dict[str, npt.NDArray]

    # Visualization controls
    channelize: panel.widgets.Checkbox
    gain: panel.widgets.FloatInput

class LinePlot( ez.Unit ):

    SETTINGS: LinePlotSettings
    STATE: LinePlotState

    INPUT_SIGNAL = ez.InputStream(AxisArray)

    def initialize( self ) -> None:
        self.STATE.x_data = np.arange(0)
        self.STATE.cds_data = dict()

        self.STATE.channelize = panel.widgets.Checkbox(name = 'Channelize', value = True)
        self.STATE.gain = panel.widgets.FloatInput(name = 'Gain', value = 1.0)
    
    def plot( self ) -> panel.viewable.Viewable:
        cds = ColumnDataSource()

        fig = figure( 
            sizing_mode = 'stretch_width', 
            title = self.SETTINGS.name, 
            output_backend = "webgl" 
        )

        lines = dict()

        @panel.io.with_lock
        async def _update( 
            fig: Figure,
            cds: ColumnDataSource, 
            lines: Dict[ str, GlyphRenderer ]
        ) -> None:

            cds_data = {**self.STATE.cds_data, **{CDS_X_DIM: self.STATE.x_data}}

            for key in list(lines.keys() - self.STATE.cds_data.keys()):
                cds.remove(key)
                fig.renderers.remove(lines[key])
                del lines[key]

            for key in list(self.STATE.cds_data.keys() - lines.keys()):
                cds.add( [], key )
                lines[ key ] = fig.line( 
                    x = CDS_X_DIM, 
                    y = key, 
                    source = cds 
                )

            cds.data = cds_data
    
        cb = panel.state.add_periodic_callback( 
            partial(_update, fig, cds, lines), 
            period = 50 
        )

        return panel.pane.Bokeh( fig )

    @property
    def controls(self) -> List[panel.viewable.Viewable]:
        return [
            self.STATE.channelize,
            self.STATE.gain,
        ]

    def panel(self) -> panel.viewable.Viewable:
        return panel.Row(
            self.plot(),
            panel.Column(
                "__Line Plot Controls__",
                *self.controls
            )
        )
    
    @ez.subscriber(INPUT_SIGNAL)
    async def on_signal(self, msg: AxisArray) -> None:
        axis_name = self.SETTINGS.x_axis
        if axis_name is None:
            axis_name = msg.dims[0]
        axis = msg.get_axis(axis_name)

        with msg.view2d(axis_name) as view:

            ch_names = getattr(msg, 'ch_names', None)
            if ch_names is None:
                ch_names = [f'ch_{i}' for i in range(view.shape[1])]

            self.STATE.x_data = (np.arange(view.shape[0]) * axis.gain) + axis.offset
            vis_view = (view * self.STATE.gain.value)

            if self.STATE.channelize.value:
                vis_view += np.arange(len(ch_names)) 

            self.STATE.cds_data = {
                ch_name: vis_view[:, ch_idx] 
                for ch_idx, ch_name in enumerate(ch_names)
            }