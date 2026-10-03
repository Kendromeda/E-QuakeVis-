(function (root) {
  'use strict';
  const bindings = new WeakMap();
  async function render(div, data, state, onSelect) {
    const figure = root.SeismicPlots.buildSection(data, state);
    await root.Plotly.react(div, figure.data, figure.layout, figure.config);
    const existing = bindings.get(div);
    if (existing) existing.callback = onSelect;
    else {
      const binding = {callback: onSelect};
      div.on('plotly_click', event => {
        const id = event.points && event.points[0] && event.points[0].customdata;
        if (id != null && binding.callback) binding.callback(id);
      });
      bindings.set(div, binding);
    }
    return div;
  }
  root.SeismicSection = {render};
})(window);
