import asyncio
from dataclasses import field, replace
from typing import AsyncGenerator, List, Optional

import ezmsg.core as ez
import panel
from ezmsg.util.messages.axisarray import AxisArray

from .tabbedapp import Tab

try:
    from ezmsg.sigproc.spectral import (
        SpectralOutput,
        SpectralTransform,
        Spectrum,
        SpectrumSettings,
        WindowFunction,
    )
    from ezmsg.sigproc.window import Window, WindowSettings
except ImportError:
    raise ImportError("You need to install ezmsg-sigproc for this functionality")

from param.parameterized import Event

from .lineplot import LinePlot, LinePlotSettings
from .util import AxisScale


class SpectrumControlSettings(ez.Settings):
    spectrum_settings: SpectrumSettings = field(default_factory=SpectrumSettings)
    window_settings: WindowSettings = field(default_factory=WindowSettings)


class SpectrumControlState(ez.State):
    spectrum_queue: "asyncio.Queue[SpectrumSettings]"
    window_queue: "asyncio.Queue[WindowSettings]"

    # Controls for Spectrum
    window: panel.widgets.Select
    transform: panel.widgets.Select
    output: panel.widgets.Select

    # Controls for Window
    window_dur: panel.widgets.FloatInput
    window_shift: panel.widgets.FloatInput


class SpectrumControl(ez.Unit):
    SETTINGS = SpectrumControlSettings
    STATE = SpectrumControlState

    OUTPUT_SPECTRUM_SETTINGS = ez.OutputStream(SpectrumSettings)
    OUTPUT_WINDOW_SETTINGS = ez.OutputStream(WindowSettings)

    async def initialize(self) -> None:
        self.STATE.spectrum_queue = asyncio.Queue()
        self.STATE.window_queue = asyncio.Queue()

        # Spectrum Settings
        self.STATE.window = panel.widgets.Select(
            name="Window Function",
            options=WindowFunction.options(),
            value=self.SETTINGS.spectrum_settings.window.value,
        )

        self.STATE.transform = panel.widgets.Select(
            name="Spectral Transform",
            options=SpectralTransform.options(),
            disabled_options=[SpectralTransform.RAW_COMPLEX.value],
            value=self.SETTINGS.spectrum_settings.transform.value,
        )

        self.STATE.output = panel.widgets.Select(
            name="Spectral Output",
            options=SpectralOutput.options(),
            value=self.SETTINGS.spectrum_settings.output.value,
        )

        def queue_spectrum_settings(*events: Event) -> None:
            self.STATE.spectrum_queue.put_nowait(
                replace(
                    self.SETTINGS.spectrum_settings,
                    window=WindowFunction(self.STATE.window.value),
                    transform=SpectralTransform(self.STATE.transform.value),
                    output=SpectralOutput(self.STATE.output.value),
                )
            )

        self.STATE.window.param.watch(queue_spectrum_settings, "value")
        self.STATE.transform.param.watch(queue_spectrum_settings, "value")
        self.STATE.output.param.watch(queue_spectrum_settings, "value")

        # Window Settings
        self.STATE.window_dur = panel.widgets.FloatInput(
            name="Window Duration (sec)",
            value=self.SETTINGS.window_settings.window_dur,
            step=1e-1,
            start=0.0,
        )

        self.STATE.window_shift = panel.widgets.FloatInput(
            name="Window Shift (sec)",
            value=self.SETTINGS.window_settings.window_shift,
            step=1e-1,
            start=0.0,
        )

        def queue_window_settings(*events: Event) -> None:
            self.STATE.window_queue.put_nowait(
                replace(
                    self.SETTINGS.window_settings,
                    window_dur=self.STATE.window_dur.value,
                    window_shift=self.STATE.window_shift.value,
                )
            )

        self.STATE.window_dur.param.watch(queue_window_settings, "value")
        self.STATE.window_shift.param.watch(queue_window_settings, "value")

    @ez.publisher(OUTPUT_SPECTRUM_SETTINGS)
    async def pub_spectrum_settings(self) -> AsyncGenerator:
        while True:
            settings = await self.STATE.spectrum_queue.get()
            yield self.OUTPUT_SPECTRUM_SETTINGS, settings

    @ez.publisher(OUTPUT_WINDOW_SETTINGS)
    async def pub_window_settings(self) -> AsyncGenerator:
        while True:
            settings = await self.STATE.window_queue.get()
            yield self.OUTPUT_WINDOW_SETTINGS, settings

    @property
    def controls(self) -> List[panel.viewable.Viewable]:
        return [
            self.STATE.window,
            self.STATE.transform,
            self.STATE.output,
            self.STATE.window_dur,
            self.STATE.window_shift,
        ]


class SpectrumPlotSettings(ez.Settings):
    name: str = "Spectral Plot"
    time_axis: Optional[str] = None  # If none, use dim 0
    freq_axis: Optional[str] = "freq"  # If none; use same dim name for freq output
    freq_axis_scale: AxisScale = AxisScale.LOG
    window_dur: float = 1.0  # sec
    window_shift: float = 0.5  # sec


class SpectrumPlot(ez.Collection, Tab):
    SETTINGS = SpectrumPlotSettings

    INPUT_SIGNAL = ez.InputStream(AxisArray)

    SPECTRUM_CONTROL = SpectrumControl()
    WINDOW = Window()
    SPECTRUM = Spectrum()
    PLOT = LinePlot()

    def configure(self) -> None:
        self.PLOT.apply_settings(
            LinePlotSettings(
                name=self.SETTINGS.name,
                x_axis=self.SETTINGS.freq_axis,
                x_axis_scale=AxisScale.LOG,
            )
        )

        spectrum_settings = SpectrumSettings(
            axis=self.SETTINGS.time_axis, out_axis=self.SETTINGS.freq_axis
        )

        self.SPECTRUM.apply_settings(spectrum_settings)

        window_settings = WindowSettings(
            axis=self.SETTINGS.time_axis,
            window_dur=self.SETTINGS.window_dur,
            window_shift=self.SETTINGS.window_shift,
        )

        self.WINDOW.apply_settings(window_settings)

        self.SPECTRUM_CONTROL.apply_settings(
            SpectrumControlSettings(
                spectrum_settings=spectrum_settings, window_settings=window_settings
            )
        )

    @property
    def title(self) -> str:
        return self.SETTINGS.name

    def content(self) -> panel.viewable.Viewable:
        return self.PLOT.plot()

    def sidebar(self) -> panel.viewable.Viewable:
        return panel.Column(
            "__Line Plot Controls__",
            *self.PLOT.controls,
            "__Spectrum Settings__",
            *self.SPECTRUM_CONTROL.controls,
        )

    def panel(self) -> panel.viewable.Viewable:
        return panel.Row(
            self.PLOT.plot(),
            panel.Column(
                "__Line Plot Controls__",
                *self.PLOT.controls,
                "__Spectrum Settings__",
                *self.SPECTRUM_CONTROL.controls,
            ),
        )

    def network(self) -> ez.NetworkDefinition:
        return (
            (
                self.SPECTRUM_CONTROL.OUTPUT_SPECTRUM_SETTINGS,
                self.SPECTRUM.INPUT_SETTINGS,
            ),
            (self.SPECTRUM_CONTROL.OUTPUT_WINDOW_SETTINGS, self.WINDOW.INPUT_SETTINGS),
            (self.INPUT_SIGNAL, self.WINDOW.INPUT_SIGNAL),
            (self.WINDOW.OUTPUT_SIGNAL, self.SPECTRUM.INPUT_SIGNAL),
            (self.SPECTRUM.OUTPUT_SIGNAL, self.PLOT.INPUT_SIGNAL),
        )
