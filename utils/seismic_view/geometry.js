/* Geographic geometry on the IUGG mean-radius sphere (6371.0088 km).
 * Distances are spherical geodesics, an approximation to WGS84 ellipsoidal
 * distances. Longitudes/latitudes are degrees; distances/depths are km.
 * Depth is positive down. Corridor width is HALF-width, with flat end caps.
 * crossTrackKm is positive to the left when looking from A toward A-prime.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.SeismicGeometry = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const R = 6371.0088;
  const RAD = Math.PI / 180;
  const EPS = 1e-9;
  const clamp = (x, low, high) => Math.max(low, Math.min(high, x));
  const wrap = lon => ((lon + 180) % 360 + 360) % 360 - 180;
  const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
  const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
  const norm = a => Math.hypot(...a);
  const mix = (a, x, b, y) => a.map((value, i) => value*x + b[i]*y);

  function validCoordinate(lon, lat) {
    return Number.isFinite(lon) && Number.isFinite(lat) && Math.abs(lon) <= 180 && Math.abs(lat) <= 90;
  }

  function coordinate(lon, lat) {
    if (!validCoordinate(lon, lat)) throw new RangeError('Coordinates require finite longitude [-180, 180] and latitude [-90, 90].');
  }

  function vector(point) {
    if (!Array.isArray(point) || point.length !== 2) throw new TypeError('A coordinate must be [longitude, latitude].');
    coordinate(...point);
    const lon = point[0]*RAD, lat = point[1]*RAD;
    return [Math.cos(lat)*Math.cos(lon), Math.cos(lat)*Math.sin(lon), Math.sin(lat)];
  }

  function geographic(v) {
    return [wrap(Math.atan2(v[1], v[0])/RAD), Math.atan2(v[2], Math.hypot(v[0], v[1]))/RAD];
  }

  function project(lon, lat, centerLon, centerLat) {
    coordinate(lon, lat); coordinate(centerLon, centerLat);
    const phi = lat*RAD, origin = centerLat*RAD, delta = wrap(lon-centerLon)*RAD;
    const east = Math.cos(phi)*Math.sin(delta);
    const north = Math.cos(origin)*Math.sin(phi)-Math.sin(origin)*Math.cos(phi)*Math.cos(delta);
    const cosine = Math.sin(origin)*Math.sin(phi)+Math.cos(origin)*Math.cos(phi)*Math.cos(delta);
    const sine = Math.hypot(east, north), angle = Math.atan2(sine, cosine);
    if (Math.PI-angle < 1e-10) throw new RangeError('Projection is undefined at the antipode.');
    if (angle < 1e-14) return [0, 0];
    return [R*angle*east/sine, R*angle*north/sine];
  }

  function inverseProject(east, north, centerLon, centerLat) {
    coordinate(centerLon, centerLat);
    if (!Number.isFinite(east) || !Number.isFinite(north)) throw new RangeError('Projection coordinates must be finite kilometres.');
    const radius = Math.hypot(east, north), angle = radius/R;
    if (angle >= Math.PI-1e-10) throw new RangeError('Projection coordinates must lie inside the antipodal radius.');
    if (radius < 1e-10) return [centerLon, centerLat];
    const phi = centerLat*RAD, sine = Math.sin(angle), cosine = Math.cos(angle);
    const lat = Math.asin(clamp(cosine*Math.sin(phi)+north*sine*Math.cos(phi)/radius, -1, 1));
    const lon = centerLon*RAD+Math.atan2(east*sine, radius*Math.cos(phi)*cosine-north*Math.sin(phi)*sine);
    return [wrap(lon/RAD), lat/RAD];
  }

  function frame(a, b, widthKm) {
    const start = vector(a), end = vector(b), normalRaw = cross(start, end);
    const sine = norm(normalRaw), angle = Math.atan2(sine, dot(start, end));
    if (!Number.isFinite(widthKm) || widthKm <= 0 || widthKm > 500) throw new RangeError('Corridor half-width must be greater than 0 and at most 500 km.');
    if (angle < 1e-10 || Math.PI-angle < 1e-10) throw new RangeError('Profile endpoints must be distinct and not antipodal.');
    if (angle*R > 10000) throw new RangeError('Profile length must be at most 10000 km.');
    const normal = normalRaw.map(x => x/sine);
    return {start, normal, tangent: cross(normal, start), angle, lengthKm: angle*R};
  }

  function profileGeometry(a, b, widthKm, samples = 201) {
    const f = frame(a, b, widthKm);
    if (!Number.isInteger(samples) || samples < 2 || samples > 10001) throw new RangeError('Profile samples must be an integer between 2 and 10001.');
    const distances = Array.from({length: samples}, (_, i) => f.lengthKm*i/(samples-1));
    const vectors = distances.map(distance => mix(f.start, Math.cos(distance/R), f.tangent, Math.sin(distance/R)));
    const offset = widthKm/R;
    return {
      lengthKm: f.lengthKm, distances,
      points: vectors.map(geographic),
      left: vectors.map(p => geographic(mix(p, Math.cos(offset), f.normal, Math.sin(offset)))),
      right: vectors.map(p => geographic(mix(p, Math.cos(offset), f.normal, -Math.sin(offset))))
    };
  }

  function sliceEvents(events, a, b, widthKm) {
    const f = frame(a, b, widthKm);
    if (!Array.isArray(events)) throw new TypeError('Earthquakes must be an array.');
    return events.flatMap(event => {
      if (!event || !validCoordinate(event.lon, event.lat) || !Number.isFinite(event.depth)) return [];
      const p = vector([event.lon, event.lat]);
      const distanceKm = R*Math.atan2(dot(p, f.tangent), dot(p, f.start));
      const crossTrackKm = R*Math.asin(clamp(dot(p, f.normal), -1, 1));
      if (distanceKm < -EPS || distanceKm > f.lengthKm+EPS || Math.abs(crossTrackKm) > widthKm+EPS) return [];
      return [{...event, distanceKm: clamp(distanceKm, 0, f.lengthKm), crossTrackKm}];
    });
  }

  function axis(values, longitude) {
    if (!Array.isArray(values) || values.length < 2 || values.some(v => !Number.isFinite(v))) throw new TypeError('Slab grid axes require at least two finite coordinates.');
    // Unwrap a dateline-spanning longitude axis without changing source data.
    const result = longitude ? values.reduce((all, value) => [...all, all.length ? all[all.length-1]+wrap(value-all[all.length-1]) : value], []) : [...values];
    const direction = Math.sign(result[1]-result[0]);
    if (!direction || result.some((v, i) => i > 0 && Math.sign(v-result[i-1]) !== direction)) throw new RangeError('Slab grid axes must be strictly monotonic.');
    return result;
  }

  function prepareGrid(grid) {
    if (!grid || !Array.isArray(grid.depth)) throw new TypeError('A slab grid requires lon, lat and depth arrays.');
    const lon = axis(grid.lon, true), lat = axis(grid.lat, false);
    if (lat.some(v => Math.abs(v) > 90) || Math.abs(lon[lon.length-1]-lon[0]) >= 360) throw new RangeError('Slab grid coordinate range is invalid.');
    if (grid.depth.length !== lat.length || grid.depth.some(row => !Array.isArray(row) || row.length !== lon.length)) throw new TypeError('Slab grid depth dimensions must match latitude rows and longitude columns.');
    return {lon, lat, depth: grid.depth};
  }

  function bracket(values, value) {
    const direction = Math.sign(values[1]-values[0]);
    if ((value-values[0])*direction < -EPS || (value-values[values.length-1])*direction > EPS) return null;
    let low = 0, high = values.length-1;
    while (high-low > 1) {
      const middle = Math.floor((low+high)/2);
      if ((value-values[middle])*direction >= 0) low = middle;
      else high = middle;
    }
    const fraction = (value-values[low])/(values[high]-values[low]);
    const weight = Math.abs(value-values[low]) < EPS ? 0 : Math.abs(value-values[high]) < EPS ? 1 : clamp(fraction, 0, 1);
    return [low, high, weight];
  }

  function interpolate(grid, lon, lat) {
    coordinate(lon, lat);
    const center = (grid.lon[0]+grid.lon[grid.lon.length-1])/2;
    const x = bracket(grid.lon, lon+360*Math.round((center-lon)/360));
    const y = bracket(grid.lat, lat);
    if (!x || !y) return null;
    const vertices = [[y[0],x[0],(1-y[2])*(1-x[2])], [y[0],x[1],(1-y[2])*x[2]],
      [y[1],x[0],y[2]*(1-x[2])], [y[1],x[1],y[2]*x[2]]];
    const contributing = vertices.filter(v => v[2] > 0);
    if (contributing.some(([row,col]) => !Number.isFinite(grid.depth[row][col]))) return null;
    return contributing.reduce((sum, [row,col,weight]) => sum+grid.depth[row][col]*weight, 0);
  }

  function bilinearDepth(grid, lon, lat) {
    return interpolate(prepareGrid(grid), lon, lat);
  }

  function sampleSlabProfile(grid, profile) {
    const prepared = prepareGrid(grid);
    if (!profile || !Array.isArray(profile.points) || !Array.isArray(profile.distances) || profile.points.length !== profile.distances.length) throw new TypeError('A profile requires matching points and distances.');
    return profile.points.map(([lon,lat], index) => ({
      distanceKm: profile.distances[index], depth: interpolate(prepared,lon,lat), lon, lat
    }));
  }

  return Object.freeze({project, inverseProject, profileGeometry, sliceEvents, bilinearDepth, sampleSlabProfile});
}));
