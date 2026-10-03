(function (root) {
  'use strict';
  const bindings = new WeakMap();
  function dispatch(binding, point) {
    if (binding.drawing) {
      if (Number.isFinite(point.x) && Number.isFinite(point.y) && binding.onPick) {
        const ll = root.SeismicGeometry.inverseProject(point.x, point.y, ...binding.center);
        if (ll.every(Number.isFinite)) binding.onPick(ll);
      }
      return;
    }
    if (point.customdata != null && binding.onSelect) binding.onSelect(point.customdata);
  }
  function bindClicks(div) {
    let gesture = null;
    const moved = event => gesture && Math.hypot(event.clientX-gesture.x, event.clientY-gesture.y) > 5;
    const deliver = point => {
      if (gesture) {
        if (gesture.cancelled || gesture.handled || gesture.binding !== bindings.get(div)) return;
        gesture = {...gesture, handled: true};
      }
      dispatch(bindings.get(div), point);
    };
    if (div.addEventListener) {
      const target = root.addEventListener ? root : div;
      div.addEventListener('pointerdown', event => {
        const cancelled = (event.button != null && event.button !== 0) || Boolean(gesture && gesture.pressed);
        gesture = {id: event.pointerId, x: event.clientX, y: event.clientY, pressed: true,
          cancelled, handled: false, point: null, binding: bindings.get(div)};
      }, true);
      target.addEventListener('pointermove', event => {
        if (gesture && gesture.pressed && event.pointerId === gesture.id && moved(event)) {
          gesture = {...gesture, cancelled: true};
        }
      }, true);
      target.addEventListener('pointerup', event => {
        if (!gesture || event.pointerId !== gesture.id) return;
        gesture = {...gesture, pressed: false, cancelled: gesture.cancelled || moved(event)};
        if (gesture.point) deliver(gesture.point);
      }, true);
      target.addEventListener('pointercancel', event => {
        if (gesture && event.pointerId === gesture.id) gesture = {...gesture, pressed: false, cancelled: true};
      }, true);
    }
    // GL3D may emit while the mouse is held; defer its hit until an actual click
    // completes. Keep the completed gesture to reject repeated hits after release.
    div.on('plotly_click', event => {
      const point = event.points && event.points[0];
      if (!point) return;
      if (gesture && gesture.pressed) gesture = {...gesture, point};
      else deliver(point);
    });
  }
  function retainedCamera(div, fallback) {
    const camera = div._fullLayout && div._fullLayout.scene && div._fullLayout.scene.camera;
    if (!camera) return fallback;
    return Object.fromEntries(['eye', 'up', 'center', 'projection']
      .filter(key => camera[key]).map(key => [key, {...camera[key]}]));
  }
  async function render(div, data, state, onSelect, onPick) {
    const figure = root.SeismicPlots.build3D(data, state, root.SeismicGeometry);
    const layout = {...figure.layout, scene: {...figure.layout.scene,
      camera: retainedCamera(div, figure.layout.scene.camera)}};
    await root.Plotly.react(div, figure.data, layout, figure.config);
    const existing = bindings.has(div);
    bindings.set(div, {onSelect, onPick, drawing: Boolean(state.drawing), center: [...data.center]});
    if (!existing) bindClicks(div);
    return div;
  }
  root.Seismic3D = {render};
})(window);
