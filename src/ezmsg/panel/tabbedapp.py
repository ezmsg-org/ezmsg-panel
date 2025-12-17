import typing

import panel as pn


class Tab:
    @property
    def title(self) -> str:
        return "Tab"

    def sidebar(self) -> pn.viewable.Viewable:
        return pn.Card(
            title=f"# {self.title} Sidebar",
        )

    def content(self) -> pn.viewable.Viewable:
        return pn.Card(title=f"# {self.title} Content", sizing_mode="stretch_both")

    def app(self) -> pn.template.FastListTemplate:
        return _create_app(self.title, [self])


class TabbedApp:
    @property
    def title(self) -> str:
        return ""

    @property
    def tabs(self) -> typing.List[Tab]:
        return []

    def app(self) -> pn.template.FastListTemplate:
        return _create_app(self.title, self.tabs)


def _create_app(title: str, tabs: typing.List[Tab]) -> pn.template.FastListTemplate:
    tab_names = [t.title for t in tabs]

    main = pn.Column(sizing_mode="stretch_both")

    sidebar = pn.Column(sizing_mode="stretch_both")

    template_sidebar = [sidebar]

    def populate(tab: str) -> None:
        tab_idx = tab_names.index(tab)
        sidebar.clear()
        sidebar.append(tabs[tab_idx].sidebar())
        main.clear()
        main.append(tabs[tab_idx].content())

    if len(tabs) > 1:
        tab_buttons = pn.widgets.RadioButtonGroup(
            name="Tab Select",
            options=tab_names,
            button_style="outline",
            sizing_mode="stretch_width",
            orientation="vertical",
        )

        @pn.depends(tab_buttons, watch=True)
        def tab_changed(tab: str) -> None:
            populate(tab)

        template_sidebar = [tab_buttons, pn.layout.Divider()] + template_sidebar

    if len(tab_names):
        populate(tab_names[0])

    template = pn.template.FastListTemplate(
        title=title,
        sidebar=template_sidebar,
        main=[main],
    )

    return template
