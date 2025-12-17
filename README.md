# ezmsg-panel
Griffin Milsap 2024


This module serves as a pattern showing how to use [Panel](https://github.com/holoviz/panel.git) to create interactive dashboards and data visualizations within [ezmsg](https://github.com/ezmsg-org/ezmsg).

At the moment, this module should be considered more of a pattern/example than a fully supported extension that provides panel functionality.  That said, there are some (currently) useful classes in here that provide some shared functionality that are used in other ezmsg projects and modules, so it is included in [ezmsg-org](https://github.com/ezmsg-org) and PyPI because of that.

## Discussion of the Pattern
This module should be considered as more of a pattern/example on how to use Panel in ezmsg; and as such, it will not be documented traditionally; rather, the pattern will be explained using the [`examples/eeg_demo.py`](https://github.com/griffinmilsap/ezmsg-panel/blob/b745dae42f44c551d3932e79136fb3d7a7f3f8c7/examples/eeg_demo.py) example.

### Creating Widgets for a Unit
One of the primary uses for Panel within an ezmsg context is to create a frontend dashboard with widgets that let you control the way units behave in a system.  This is best accomplished by putting Panel widgets in the state of the unit and providing an attribute that exposes those widgets.  For example, in [`timeseriesplot.py`](https://github.com/griffinmilsap/ezmsg-panel/blob/b745dae42f44c551d3932e79136fb3d7a7f3f8c7/src/ezmsg/panel/timeseriesplot.py), we have a unit that publishes a new `ButterworthFilterSettings` message for each time the widgets change value.  These widgets are exposed using the `controls` method here.  This is one possible way to expose these widgets, but you could use a property or manually grab the `panel.viewable.Viewable`s to compose with other dashboard content.

``` python
class ButterworthFilterControlState(ez.State):
    queue: "asyncio.Queue[ButterworthFilterSettings]"

    # Controls for Butterworth Filter
    order: panel.widgets.IntInput
    cuton: panel.widgets.FloatInput
    cutoff: panel.widgets.FloatInput


class ButterworthFilterControl(ez.Unit):
    SETTINGS = ButterworthFilterSettings
    STATE = ButterworthFilterControlState

    OUTPUT_SETTINGS = ez.OutputStream(ButterworthFilterSettings)

    def initialize(self) -> None:
        self.STATE.queue = asyncio.Queue()

        # Spectrum Settings
        self.STATE.order = panel.widgets.IntInput( 
            name = 'Filter Order (0 = "Disabled")', 
            value = 0, 
            start = 0 
        )

        self.STATE.cuton = panel.widgets.FloatInput( 
            name = 'Filter Cuton (Hz)', 
            value = 1.0, 
            start = 0.0 
        )

        self.STATE.cutoff = panel.widgets.FloatInput( 
            name = 'Filter Cutoff (Hz)', 
            value = 30.0, 
            start = 0.0 
        )

        def enqueue_design(*events: Event) -> None:
            self.STATE.queue.put_nowait(replace( 
                self.SETTINGS,
                order = self.STATE.order.value,
                cuton = self.STATE.cuton.value,
                cutoff = self.STATE.cutoff.value
            ))

        self.STATE.order.param.watch(enqueue_design, 'value')
        self.STATE.cuton.param.watch(enqueue_design, 'value')
        self.STATE.cutoff.param.watch(enqueue_design, 'value')


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
            title = 'Butterworth Filter Controls',
            collapsed = True,
            sizing_mode = 'stretch_width'
        )
```

### Composing Widgets/content to create an "App"
This `Card` can be composed with other visual content into an "Application" using the `Tab` wrapper around Panel's [`FastListTemplate`](https://panel.holoviz.org/reference/templates/FastListTemplate.html).  `FastListTemplate` provides a sidebar and content area to display controls and plots/content respectively.  `Tab` can be used in `TabbedApp` to provide multiple "apps" bundled together on one page, but beware performance issues can arise when bundling too many `Tab`s together, especially if they have dynamically updating plot content.  Again, from [`timeseriesplot.py`](https://github.com/griffinmilsap/ezmsg-panel/blob/b745dae42f44c551d3932e79136fb3d7a7f3f8c7/src/ezmsg/panel/timeseriesplot.py)

``` python
class TimeSeriesPlot(ez.Collection, Tab):
    SETTINGS = TimeSeriesPlotSettings

    INPUT_SIGNAL = ez.InputStream(AxisArray)

    BPFILT = ButterworthFilter()
    QUEUE = MessageQueue(MessageQueueSettings(maxsize = 10, leaky = True))
    BPFILT_CONTROL = ButterworthFilterControl()
    PLOT = ScrollingLinePlot()

    @property
    def title(self) -> str:
        return self.SETTINGS.name
    
    def content(self) -> panel.viewable.Viewable:
        return self.PLOT.content()
    
    def sidebar(self) -> panel.viewable.Viewable:
        return panel.Column(
            self.PLOT.sidebar(),
            self.BPFILT_CONTROL.controls()
        )

    def configure(self) -> None:
        self.PLOT.apply_settings(self.SETTINGS)

        filter_settings = ButterworthFilterSettings(
            axis = self.SETTINGS.time_axis
        )

        self.BPFILT_CONTROL.apply_settings(filter_settings)
        self.BPFILT.apply_settings(filter_settings)

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.BPFILT_CONTROL.OUTPUT_SETTINGS, self.BPFILT.INPUT_FILTER),
            (self.INPUT_SIGNAL, self.BPFILT.INPUT_SIGNAL),
            (self.BPFILT.OUTPUT_SIGNAL, self.QUEUE.INPUT),
            (self.QUEUE.OUTPUT, self.PLOT.INPUT_SIGNAL),
        )
```

Note here that `title`, `content` and `sidebar` are defined in the `Tab` superclass, but can be overridden in this subclass to specify content from subunits that will be rendered together in this app.  `Tab` provides the `app` method that assembles a `FastListTemplate` from these methods and provides this to the server.

### Serving the App using Panel
Panel supports a variety of usages, with the most common way of launching Panel apps to be `panel serve`.  Unfortunately, ezmsg needs the main execution context and we need a way to serve Panel apps from within ezmsg.  This is accomplished using `panel.serve` in an `ez.task`.  The pattern espoused by this module separates the Panel server from the actual panel apps which allows you to couple the apps and UIs more closely with the ez.Components and functionality they provide.  The basis of this is `Application`, which has a borderline trivial implementation thanks to `panel.serve` being a non-blocking function call that sets up the server on the current `asyncio` loop.

``` Python
class Application(ez.Unit):
    SETTINGS = ApplicationSettings

    panels: Mapping[str, 'TViewableOrFunc']

    @ez.task
    async def serve(self) -> None:
        if self.SETTINGS.port is not None:
            if hasattr(self, 'panels'):
                panel.serve( 
                    self.panels,
                    port = self.SETTINGS.port,
                    title = self.SETTINGS.name,
                    websocket_origin = '*',
                    **self.SETTINGS.serve_kwargs
                )
            else:
                ez.logger.warning("Panel application has no panels set. " + \
                    "Did you forget to configure the panels attribute?"
                )
```

Instantiating this Unit will create a Panel server, and you must provide content for it to serve.  This is done by setting the Unit's `panels` attribute directly with a dictionary of apps to serve.  From [`examples/eeg_demo.py`](https://github.com/griffinmilsap/ezmsg-panel/blob/b745dae42f44c551d3932e79136fb3d7a7f3f8c7/examples/eeg_demo.py):

``` python
class EEGSpectrum(ez.Collection):
    SETTINGS = EEGSpectrumSettings

    APP = Application()
    EEG = EEGSynth()
    TIMESERIES_PLOT = TimeSeriesPlot()
    SPECTRUM_PLOT = SpectrumPlot()

    def configure(self) -> None:

        self.APP.apply_settings(self.SETTINGS.app_settings)
        self.EEG.apply_settings(self.SETTINGS.eeg_settings)

        self.SPECTRUM_PLOT.apply_settings(
            SpectrumPlotSettings(
                name = 'EEG Spectrum',
                time_axis = 'time'
            )
        )

        self.TIMESERIES_PLOT.apply_settings(
            TimeSeriesPlotSettings(
                name = "EEG Timeseries",
                time_axis = 'time'
            )
        )

        self.APP.panels = {
            'Timeseries': self.TIMESERIES_PLOT.app,
            'Spectra': self.SPECTRUM_PLOT.app
        }

    def network(self) -> ez.NetworkDefinition:
        return (
            (self.EEG.OUTPUT_SIGNAL, self.SPECTRUM_PLOT.INPUT_SIGNAL),
            (self.EEG.OUTPUT_SIGNAL, self.TIMESERIES_PLOT.INPUT_SIGNAL)
        )

    # IMPORTANT: ALL viewable panels must exist in SAME process 
    # as the Application.  If you need to split out computation
    # to another process, de-couple your UI/Panel unit from the compute unit.
    def process_components(self) -> Tuple[ez.Component, ...]:
        return (
            # self.TIMESERIES_PLOT, # Uncomment me and this panel doesn't work anymore!
        )
```

Note the comment on `process_components`:  When separating the `panel.serve` function call from the rest of the servable content, it is VERY IMPORTANT that all servable content resides in the SAME process as the server.  This can complicate development of apps that should live in their own process for performance reasons.  Because of this, it is recommended to somewhat decouple the visualization/frontend/view from the computation unit/backend/model it supports.  For an example of this, note that `ButterworthFilterControl` is publishing `ButterworthFilterSettings` messages to the `ButterworthFilter` (`BPFILT`) in `TimeseriesPlot`.  If the filtering would be advantageous to move to a separate process, it could be done by specifying `BPFILT` to run in a separate process, but the filter control (`BPFILT_CONTROL`) will still remain in the same process as the `Application` Unit that ultimately starts the Panel server using `panel.serve`.  

__Another important note on `APP.panels`__: This is a dictionary that is a mapping between the application name (`str`) and either a `panel.viewable.Viewable` or a `Callable` that RETURNS a `panel.viewable.Viewable`.  In this case, we are providing a `Callable`: `Tab.app` that returns a new `Viewable` for each client, composed of a bunch of shared Widgets from the STATE of sub-Units.  Its important to note though, that when we provide a __`Callable`__ instead of a `Viewable`, we have the option of providing each client _unique_ resources which is important for creating/updating [dynamic plots](#dynamic-plots).

Looking closer at `Application`'s `panel.serve` task:
``` python
@ez.task
async def serve(self) -> None:
    if self.SETTINGS.port is not None:
        if hasattr(self, 'panels'):
            panel.serve( 
                self.panels,
                port = self.SETTINGS.port,
                title = self.SETTINGS.name,
                websocket_origin = '*',
                **self.SETTINGS.serve_kwargs
            )
        else:
            ez.logger.warning("Panel application has no panels set. " + \
                "Did you forget to configure the panels attribute?"
            )
```

If the `ApplicationSettings` port argument is set to `None`, the server will not start at all.  If we set it to `0`, Panel will choose a random open port to host the server on.  Additionally, note that `websocket_origin` is set to `*`.  Without this, the internal websocket that Panel uses to host the dashboard would reject any connection from hosts other than `localhost`.  This setting allows us to host a dashboard on a headless server and connect to it from other machines, but exposes the dashboard and ultimately the underlying process to remote clients which represents a security risk.  Additionally, this dashboard is not secured using `https`.  There is likely a way to do that, but it goes beyond the scope of this documentation/implementation.

Ultimately, the dashboard/app is run as you would expect, within ezmsg:

``` python
eeg_spectrum = EEGSpectrum(
    EEGSpectrumSettings(
        eeg_settings = EEGSynthSettings(
            fs = args.fs,
            n_time = args.blocksize,
            alpha_freq = args.alpha,
            n_ch = args.channels
        ),

        app_settings = ApplicationSettings(
            port = args.port,
            name = 'EEG Spectrum Example'
        )
    )
)

ez.run(SYSTEM = eeg_spectrum)
```

Again note that `ApplicationSettings` default value for `port` is None, so by default the server will NOT start.  You will need to specify a value of `0` to actually launch the Panel app, or specify a specific port you'd like yo use.

### Dynamic plots
One of the most useful aspects of Panel within ezmsg is visualizing data flowing through the system.  Sometimes this is best done using a dynamically updating plot.  `TimeSeriesPlot` has a `ScrollingLinePlot` that displays scrolls time-varying data from `AxisArrays` in a scrolling plot.  This is done using `bokeh` directly, but there's a few caveats to be aware of.

The widget examples that have been described thus far just expose a shared `panel.viewable.Viewable` to be rendered for all conneccted clients(browsers).  When one connected client changes the value of a widget, it is shared by all clients and is simultaneously updated, as expected.  `panel.pane.Bokeh` plots can be shared the same way, but only if they're __static__.  If you want a plot to change and update for clients, each client needs to have its own instance of the plot, and your code has to update all of those plots with a lock (`panel.io.with_lock`) that allows your code to modify bokeh models directly.  Additionally, it's useful to decouple updating the plot from the incoming message rate for performance reasons, using `panel.io.PeriodicCallback`.  Note that scheduling a `PeriodicCallback` wrapped `with_lock` is the only way to call `ColumnDataSource.stream` or `ColumnDataSource.update`

To accomplish this, we create a method in our Unit that returns a new panel.Pane.Bokeh with a unique `figure` that will be given to every client, and register a `PeriodicCallback` with a callback function that can be used to update that figure for that client.  We also maintain a separate `ColumnDataSource` for each client and create an `asyncio.Queue` for data for each client and add that queue to the `STATE` of this unit.  We also register a callback handler to remove the queue when the session is destroyed so that we aren't updating clients that have disconnected.

_(This is a much more complicated example, but it describes the `examples/eeg_demo.py` functionality which has been the topic of this discussion.  A simpler basic bokeh example pattern is available in `examples/bokeh_model.py`)_

From [`scrollinglineplot.py`](https://github.com/griffinmilsap/ezmsg-panel/blob/b745dae42f44c551d3932e79136fb3d7a7f3f8c7/src/ezmsg/panel/scrollinglineplot.py):

``` python
    def plot( self ) -> panel.viewable.Viewable:
        queue: "asyncio.Queue[Dict[str, np.ndarray]]" = asyncio.Queue()
        cds = ColumnDataSource( { CDS_TIME_DIM: [ self.STATE.cur_t ] } )
        fig = figure( 
            sizing_mode = 'stretch_width', 
            title = self.SETTINGS.name, 
            output_backend = "webgl", 
            tooltips=[("x", "$x"), ("y", "$y")]
        )

        lines = {}

        @panel.io.with_lock
        async def _update( 
            fig: figure,
            cds: ColumnDataSource, 
            queue: "asyncio.Queue[ Dict[ str, np.ndarray ] ]",
            lines: Dict[ str, GlyphRenderer ]
        ) -> None:
            while not queue.empty():
                cds_data: Dict[ str, np.ndarray ] = queue.get_nowait()

                ch_names = [ ch for ch in cds_data.keys() if ch != CDS_TIME_DIM ]
                offsets = np.arange( len( ch_names ) ) if self.STATE.channelize.value else np.zeros( len( ch_names ) )

                cds_data = { 
                    ch: ( arr * self.STATE.gain.value ) + offsets[ ch_names.index( ch ) ] 
                    if ch != CDS_TIME_DIM else arr 
                    for ch, arr in cds_data.items() 
                }

                # Add new lines to plot as necessary
                # TODO: Remove lines from plot as necessary
                for key, arr in cds_data.items():
                    if key not in cds.column_names:
                        cds.add( [ arr[0] ], key )
                        lines[ key ] = fig.line( 
                            x = CDS_TIME_DIM, 
                            y = key, 
                            source = cds 
                        )

                cds.stream( cds_data, rollover = int( self.STATE.duration.value * self.STATE.cur_fs ) )
    
        cb = panel.state.add_periodic_callback( 
            partial(_update, fig, cds, queue, lines), 
            period = 50 
        )

        def remove_queue(_: BokehSessionContext) -> None:
            self.STATE.queues.remove(queue)
        self.STATE.queues.add(queue)
        panel.state.on_session_destroyed(remove_queue)

        return panel.pane.Bokeh(fig)
```

When we receive data that we want to push to all clients, we simply iterate through all the queues in the `STATE` and update all clients by pushing this new data onto the queue for every one of them.

``` python
    @ez.subscriber( INPUT_SIGNAL )
    async def on_signal( self, msg: AxisArray ) -> None:
        axis_name = self.SETTINGS.time_axis
        if axis_name is None:
            axis_name = msg.dims[0]
        axis = msg.get_axis(axis_name)
        fs = 1.0 / axis.gain

        with msg.view2d(axis_name) as view:
            
            ch_names = getattr( msg, 'ch_names', None )
            if ch_names is None:
                ch_names = [ f'ch_{i}' for i in range( view.shape[1] ) ]

            t = ( np.arange(view.shape[0]) / fs ) + self.STATE.cur_t
            cds_data = { CDS_TIME_DIM: t }
            for ch_idx, ch_name in enumerate( ch_names ):
                cds_data[ ch_name ] = view[ :, ch_idx ]

            self.STATE.cur_fs = fs
            self.STATE.cur_t += view.shape[0] / fs
            self.STATE.fs.value = fs
            self.STATE.n_time.value = view.shape[0]

            for queue in self.STATE.queues:
                queue.put_nowait( cds_data )
```

Do note that there will be a performance hit directly proportional to the number of connected clients, as well as the update rate of your plots.  We also note that there seems to be some sort of resource leak in current Panel or bokeh (unsure) that causes updates to slow to a crawl if a session is maintained for a long time.

## Development

We use [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for development.

1. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) if not already installed.
2. Fork this repository and clone your fork locally.
3. Open a terminal and `cd` to the cloned folder.
4. Run `uv sync` to create a `.venv` and install dependencies.
5. (Optional) Install pre-commit hooks: `uv run pre-commit install`
6. After making changes, run the test suite: `uv run pytest tests`

## License

MIT License - see [LICENSE](LICENSE) for details.
