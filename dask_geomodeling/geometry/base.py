"""
Module containing the base geometry block classes.
"""
import pandas as pd
from dask_geomodeling import Block

__all__ = ["GeometryBlock", "GetSeriesBlock", "SetSeriesBlock"]


class GeometryBlock(Block):
    """ The base block for geometries

    All geometry blocks must be derived from this base class and must implement
    the following attributes:

    - ``columns``: a set of column names to expect in the dataframe

    A geometry request contains the following fields:

    - mode: one of ``{"intersects", "centroid", "extent"}``
    - geometry: limit returned objects to objects that intersect with this
      shapely geometry object
    - projection: projection to return the geometries in as WKT string
    - limit: the maximum number of geometries
    - min_size: geometries with a bbox that is smaller than this on all sides
      are left out
    - start: start date as UTC datetime
    - stop: stop date as UTC datetime
    - filters: dict of `Django <https://www.djangoproject.com/>`_ ORM-like
      filters on properties (e.g. ``id=598``)

    The data response contains the following:

    - if mode was ``'intersects'``: a DataFrame of features with properties
    - if mode was ``'extent'``: the bbox that contains all features

    To be able to perform operations on properties, there is a helper type
    called``SeriesBlock``. This is the block equivalent of a ``pandas.Series``.
    You can get a ``SeriesBlock`` from a ``GeometryBlock``, perform operations
    on it, and set it back into a ``GeometryBlock``.
    """

    def __getitem__(self, name):
        return GetSeriesBlock(self, name)

    def __setitem__(self, *args, **kwargs):
        raise NotImplementedError("Please use block.set to set a column.")

    def set(self, *args):
        # NB cannot use __setitem__ as block instances are immutable
        return SetSeriesBlock(self, *args)

    def to_file(self, *args, **kwargs):
        """Utility function to export data from this block to a file on disk.

        You need to specify the target file path as well as the extent geometry
        you want to save.

        Args:
          url (str): The target file path. The extension determines the format.
            For supported formats, consult
            GeometryFileSink.supported_extensions.
          fields (dict): a mapping that relates column names to output file
            field names field names,
            ``{<output file field name>: <column name>, ...}``.
          tile_size (int): Optionally use this for large exports to stay within
            memory constraints. The export is split in tiles of given size
            (units are determined by the projection). Finally the tiles are
            merged.
          geometry (shapely Geometry): Limit exported objects to objects whose
            centroid intersects with this geometry.
          projection (str): The projection as a WKT string or EPSG code.
            Sets the projection of the geometry argument, the target
            projection of the data, and the tiling projection.
          start (datetime): start date as UTC datetime
          stop (datetime): stop date as UTC datetime
          **request: see GeometryBlock request specification

        Relevant settings can be adapted as follows:
          >>> from dask import config
          >>> config.set({"geomodeling.root": '/my/output/data/path'})
          >>> config.set({"temporary_directory": '/my/alternative/tmp/dir'})
        """
        from dask_geomodeling.geometry.sinks import to_file

        return to_file(self, *args, **kwargs)


class SeriesBlock(Block):
    """ A helper block for GeometryBlocks, representing one single field"""

    def __add__(self, other):
        from . import Add

        return Add(self, other)

    def __mul__(self, other):
        from . import Multiply

        return Multiply(self, other)

    def __neg__(self):
        from . import Multiply

        return Multiply(self, -1)

    def __sub__(self, other):
        from . import Subtract

        return Subtract(self, other)

    def __floordiv__(self, other):
        from . import FloorDivide

        return FloorDivide(self, other)

    def __mod__(self, other):
        from . import Modulo

        return Modulo(self, other)

    def __truediv__(self, other):
        from . import Divide

        return Divide(self, other)

    def __pow__(self, other):
        from . import Power

        return Power(self, other)

    def __eq__(self, other):
        from . import Equal

        return Equal(self, other)

    def __ne__(self, other):
        from . import NotEqual

        return NotEqual(self, other)

    def __gt__(self, other):
        from . import Greater

        return Greater(self, other)

    def __ge__(self, other):
        from . import GreaterEqual

        return GreaterEqual(self, other)

    def __lt__(self, other):
        from . import Less

        return Less(self, other)

    def __le__(self, other):
        from . import LessEqual

        return LessEqual(self, other)

    def __invert__(self):
        from . import Invert

        return Invert(self)

    def __and__(self, other):
        from . import And

        return And(self, other)

    def __or__(self, other):
        from . import Or

        return Or(self, other)

    def __xor__(self, other):
        from . import Xor

        return Xor(self, other)


class GetSeriesBlock(SeriesBlock):
    """Get a column from a GeometryBlock.

    :param source: GeometryBlock
    :param name: name of the column to get
    :returns: SeriesBlock containing the property column

    :type source: GeometryBlock
    :type name: string
    """

    def __init__(self, source, name):
        if not isinstance(source, GeometryBlock):
            raise TypeError("'{}' object is not allowed".format(type(source)))
        if not isinstance(name, str):
            raise TypeError("'{}' object is not allowed".format(type(name)))
        if name not in source.columns:
            raise KeyError("Column '{}' is not available".format(name))
        super().__init__(source, name)

    @property
    def source(self):
        return self.args[0]

    @staticmethod
    def process(data, name):
        if "features" not in data or name not in data["features"].columns:
            return pd.Series([])
        return data["features"][name]


class SetSeriesBlock(GeometryBlock):
    """Set one or multiple columns (SeriesBlocks) in a GeometryBlock.

    :param source: source to add the extra columns to
    :param column: name of the column to be set
    :param value: series or constant value to set
    :param args: string, SeriesBlock, ..., repeated multiple times
    :returns: the source GeometryBlock with additional property columns

    :type source: GeometryBlock
    :type column: string
    :type value: SeriesBlock, scalar

    Example:
      >>> SetSeriesBlock(view, 'column_1', series_1, 'column_2', series_2)
    """

    def __init__(self, source, column, value, *args):
        if not isinstance(source, GeometryBlock):
            raise TypeError("'{}' object is not allowed".format(type(source)))
        args = (column, value) + args
        if len(args) % 2 != 0:
            raise ValueError("The number of arguments should be even")
        for column in args[::2]:
            if not isinstance(column, str):
                raise TypeError("'{}' object is not allowed".format(type(column)))
        super().__init__(source, *args)

    @property
    def source(self):
        return self.args[0]

    @property
    def columns(self):
        return self.source.columns | set(self.args[1::2])

    @staticmethod
    def process(data, *col_val_pairs):
        if "features" not in data or len(data["features"]) == 0:
            return data
        features = data["features"].copy()
        for column, value in zip(col_val_pairs[::2], col_val_pairs[1::2]):
            features[column] = value

        return {"features": features, "projection": data["projection"]}


class BaseSingle(GeometryBlock):
    """Baseclass for all geometry blocks that adjust a geometry source"""

    def __init__(self, source, *args):
        if not isinstance(source, GeometryBlock):
            raise TypeError("'{}' object is not allowed".format(type(source)))
        super().__init__(source, *args)

    @property
    def source(self):
        return self.args[0]

    @property
    def columns(self):
        return self.source.columns  # by default, columns is not changed


class BaseSingleSeries(SeriesBlock):
    """Baseclass for all series blocks that adjust a single series source"""

    def __init__(self, source, *args):
        if not isinstance(source, SeriesBlock):
            raise TypeError("'{}' object is not allowed".format(type(source)))
        super().__init__(source, *args)

    @property
    def source(self):
        return self.args[0]
