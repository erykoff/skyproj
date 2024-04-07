import functools
import numpy as np
import matplotlib.collections
from matplotlib.transforms import Bbox, Transform

from .skycrs import proj, proj_inverse
from .mpl_utils import ExtremeFinderWrapped, WrappedFormatterDMS
import mpl_toolkits.axisartist.angle_helper as angle_helper
from mpl_toolkits.axisartist.axis_artist import TickLabels


__all__ = ["SkyGridHelper", "SkyGridlines"]

"""
Plan on the "Sky Grid Helper"

In the mpl raw there is a grid helper which has a grid finder and this
extra level of indirection is confusing and unnessary.

The grid helper (SkyGridHelper) will be set up with all the necessary things.

And what it needs to do is be able to `get_gridlines()` and later something with ticks.

You need to first update_lim with an axis.

And that will set the _grid_info.

And this will possible work at this point.

"""

def _find_line_box_crossings(xys, bbox):
    """
    Find the points where a polyline crosses a bbox, and the crossing angles.

    Parameters
    ----------
    xys : (N, 2) array
        The polyline coordinates.
    bbox : `.Bbox`
        The bounding box.

    Returns
    -------
    list of ((float, float), float)
        Four separate lists of crossings, for the left, right, bottom, and top
        sides of the bbox, respectively.  For each list, the entries are the
        ``((x, y), ccw_angle_in_degrees)`` of the crossing, where an angle of 0
        means that the polyline is moving to the right at the crossing point.

        The entries are computed by linearly interpolating at each crossing
        between the nearest points on either side of the bbox edges.
    """
    crossings = []
    dxys = xys[1:] - xys[:-1]
    for sl in [slice(None), slice(None, None, -1)]:
        us, vs = xys.T[sl]  # "this" coord, "other" coord
        dus, dvs = dxys.T[sl]
        umin, vmin = bbox.min[sl]
        umax, vmax = bbox.max[sl]
        for u0, inside in [(umin, us > umin), (umax, us < umax)]:
            cross = []
            idxs, = (inside[:-1] ^ inside[1:]).nonzero()
            for idx in idxs:
                v = vs[idx] + (u0 - us[idx]) * dvs[idx] / dus[idx]
                if not vmin <= v <= vmax:
                    continue
                crossing = (u0, v)[sl]
                theta = np.degrees(np.arctan2(*dxys[idx][::-1]))
                cross.append((crossing, theta))
            crossings.append(cross)
    return crossings


class SkyGridHelper:
    """
    """
    def __init__(
        self,
        axis,
        projection,
        wrap=0.0,
        n_grid_lon_default=None,
        n_grid_lat_default=None,
        longitude_ticks="positive",
        celestial=True,
        equatorial_labels=False,
        full_circle=False,
        min_lon_ticklabel_delta=0.1,
    ):
        self._transform_lonlat_to_xy = functools.partial(proj, projection=projection, wrap=wrap)
        self._transform_xy_to_lonlat = functools.partial(proj_inverse, projection=projection)
        self._wrap = wrap
        self._n_grid_lon_default = n_grid_lon_default
        self._n_grid_lat_default = n_grid_lat_default
        self._extreme_finder = ExtremeFinderWrapped(20, 20, wrap)
        self._grid_locator_lon = None
        self._grid_locator_lat = None
        self._tick_formatter_lon = WrappedFormatterDMS(180.0, longitude_ticks)
        self._tick_formatter_lat = angle_helper.FormatterDMS()
        self._celestial = celestial
        self._equatorial_labels = equatorial_labels
        self._full_circle = full_circle
        if projection.name == "cyl":
            self._delta_cut = 80.0
        else:
            self._delta_cut = 0.5*projection.radius
        self._min_lon_ticklabel_delta = min_lon_ticklabel_delta

        self._grid_info = None
        self._old_limits = None

        self.update_lim(axis)

    def get_gridlines(self, axis="both"):
        """
        """
        if self._grid_info is None:
            raise RuntimeError("Must first call update_lim(axis)")

        grid_lines = []
        if axis in ["both", "x"]:
            for gl in self._grid_info["lon"]["lines"]:
                grid_lines.extend(self._cut_grid_line_jumps(gl))
        if axis in ["both", "y"]:
            for gl in self._grid_info["lat"]["lines"]:
                grid_lines.extend(self._cut_grid_line_jumps(gl))
        return grid_lines

    def _cut_grid_line_jumps(self, gl):
        """Check for jumps and cut gridlines into multiple sections.

        Parameters
        ----------
        gl : `list` [`tuple`]
            Input gridlines.  List of tuples of numpy arrays.

        Returns
        -------
        gl_new : `list` [`tuple`]
            New gridlines.  Jumps have been replaced with `np.nan`
            values to ensure lines are not connected around edges.
        """
        dx = gl[0][0][1:] - gl[0][0][: -1]
        dy = gl[0][1][1:] - gl[0][1][: -1]

        split, = (np.hypot(dx, dy) > self._delta_cut).nonzero()

        if split.size == 0:
            return gl

        gl_new = [(np.insert(gl[0][0], split + 1, np.nan),
                   np.insert(gl[0][1], split + 1, np.nan))]

        return gl_new

    def update_lim(self, axis):
        """
        """
        x1, x2 = axis.get_xlim()
        y1, y2 = axis.get_ylim()
        if self._old_limits != (x1, x2, y1, y2):
            _n_grid_lon, _n_grid_lat = self._compute_n_grid_from_extent(
                axis.get_extent(),
                n_grid_lon_default=self._n_grid_lon_default,
                n_grid_lat_default=self._n_grid_lat_default,
            )
            print("Making %d/%d lines" % (_n_grid_lon, _n_grid_lat))

            if self._wrap == 180.0 and not self._full_circle:
                _include_last_lon = True
            else:
                _include_last_lon = False

            self._grid_locator_lon = angle_helper.LocatorD(_n_grid_lon, include_last=_include_last_lon)
            self._grid_locator_lat = angle_helper.LocatorD(_n_grid_lat, include_last=True)

            self._update_grid(x1, y1, x2, y2)
            self._old_limits = (x1, x2, y1, y2)

    def _update_grid(self, x1, y1, x2, y2):
        self._grid_info = self.get_grid_info(x1, y1, x2, y2)

    def get_grid_info(self, x1, y1, x2, y2):
        """
        """

        extremes = self._extreme_finder(self.inv_transform_xy, x1, y1, x2, y2)

        # min & max rage of lat (or lon) for each grid line will be drawn.
        # i.e., gridline of lon=0 will be drawn from lat_min to lat_max.

        lon_min, lon_max, lat_min, lat_max = extremes
        lon_levs, lon_n, lon_factor = self._grid_locator_lon(lon_min, lon_max)
        lon_levs = np.asarray(lon_levs)
        lat_levs, lat_n, lat_factor = self._grid_locator_lat(lat_min, lat_max)
        lat_levs = np.asarray(lat_levs)

        lon_values = lon_levs[:lon_n] / lon_factor
        lat_values = lat_levs[:lat_n] / lat_factor

        lon_lines, lat_lines = self._get_raw_grid_lines(lon_values,
                                                        lat_values,
                                                        lon_min, lon_max,
                                                        lat_min, lat_max)

        bb = Bbox.from_extents(x1, y1, x2, y2).expanded(1 + 2e-10, 1 + 2e-10)

        grid_info = {
            "extremes": extremes,
            # "lon", "lat", filled below.
        }

        for idx, lon_or_lat, levs, factor, values, lines in [
                (1, "lon", lon_levs, lon_factor, lon_values, lon_lines),
                (2, "lat", lat_levs, lat_factor, lat_values, lat_lines),
        ]:
            grid_info[lon_or_lat] = gi = {
                "lines": [[l] for l in lines],
                "ticks": {"left": [], "right": [], "bottom": [], "top": []},
            }
            for (lx, ly), v, level in zip(lines, values, levs):
                # This is not what I want actually.  I need to get in here
                # and modify to get things that hit the edge!
                # But there is the thing about the alignment.
                all_crossings = _find_line_box_crossings(np.column_stack([lx, ly]), bb)
                for side, crossings in zip(
                        ["left", "right", "bottom", "top"], all_crossings):
                    for crossing in crossings:
                        gi["ticks"][side].append({"level": level, "loc": crossing})
            for side in gi["ticks"]:
                levs = [tick["level"] for tick in gi["ticks"][side]]
                # labels = self._format_ticks(idx, side, factor, levs)
                labels = ["lab"]*len(levs)
                for tick, label in zip(gi["ticks"][side], labels):
                    tick["label"] = label

        return grid_info

    def _get_raw_grid_lines(self,
                            lon_values, lat_values,
                            lon_min, lon_max, lat_min, lat_max):

        lons_i = np.linspace(lon_min, lon_max, 100)  # for interpolation
        lats_i = np.linspace(lat_min, lat_max, 100)

        lon_lines = [self.transform_xy(np.full_like(lats_i, lon), lats_i)
                     for lon in lon_values]
        lat_lines = [self.transform_xy(lons_i, np.full_like(lons_i, lat))
                     for lat in lat_values]

        return lon_lines, lat_lines

    def transform_xy(self, lon, lat):
        return np.column_stack(self._transform_lonlat_to_xy(lon, lat)).T

    def inv_transform_xy(self, x, y):
        return np.column_stack(self._transform_xy_to_lonlat(x, y)).T

    # def update_grid_locator_lon(self, new_grid_locator):
    #     self._grid_locator_lon = new_grid_locator

    # def update_grid_locator_lat(self, new_grid_locator):
    #     self._grid_locator_lat = new_grid_locator

    def get_tick_iterator(self):
        # Maybe this is where we do the thing.
        raise NotImplementedError("Oops")

    def _compute_n_grid_from_extent(self, extent, n_grid_lat_default=None, n_grid_lon_default=None):
        """Compute the number of grid lines from the extent.

        This will respect values that were set at initialization time.

        Parameters
        ----------
        extent : array-like
            Extent as [lon_min, lon_max, lat_min, lat_max].
        n_grid_lat_default : `int`, optional
            Requested number of latitude gridlines; otherwise automatic.
        n_grid_lon_default : `int`, optional
            Requested number of longitude gridlines; otherwise automatic.

        Returns
        -------
        n_grid_lon : `int`
            Number of gridlines in the longitude direction.
        n_grid_lat : `int`
            Number of gridlines in the latitude direction.
        """
        if n_grid_lat_default is None:
            n_grid_lat = 6
        if n_grid_lon_default is None:
            latscale = np.cos(np.deg2rad(np.mean(extent[2:])))
            ratio = np.clip(np.abs(extent[1] - extent[0])*latscale/(extent[3] - extent[2]), 1./3., 5./3.)
            n_grid_lon = int(np.ceil(ratio * n_grid_lat))

        return n_grid_lon, n_grid_lat


class SkyGridlines(matplotlib.collections.LineCollection):
    """
    """
    def __init__(self, segments=[], grid_helper=None, **kwargs):
        super().__init__(segments, **kwargs)

        self.set_clip_on(True)
        # Note that this will not work unless you call
        # set_clip_box(axes.bbox) before drawing.

        self._grid_helper = grid_helper

    def set_grid_helper(self, grid_helper):
        """
        """
        self._grid_helper = grid_helper

    def draw(self, renderer):
        gridlines = self._grid_helper.get_gridlines()

        self.set_segments([np.transpose(line) for line in gridlines])

        super().draw(renderer)
