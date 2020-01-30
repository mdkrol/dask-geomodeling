"""
Module containing miscellaneous raster blocks.
"""
import numpy as np

import shapely

from dask import config
from dask_geomodeling.geometry import GeometryBlock
from dask_geomodeling.utils import (
    get_uint_dtype,
    get_dtype_max,
    get_index,
    rasterize_geoseries,
)

from .base import RasterBlock, BaseSingle


__all__ = ["Clip", "Classify", "Reclassify", "Mask", "MaskBelow", "Step", "Rasterize"]


class Clip(BaseSingle):
    """
    Clip one raster to the extent of another raster.
    
    Takes two raster inputs, one raster (store) whose values are returned in the output and one raster (source) which is used as the extent. 
    Cells of the store raster are not returned if they fall outside of the extent of source.
    Result values are no data if the source raster contains a no data value. Source may also be a boolean raster, in that case False values also result in no data values.

    Args:
      store (RasterBlock): Raster whose values are returned in the output raster
      source (RasterBlock): Raster which is used as the extent of the output raster

    Returns:
      RasterBlock which has values from store and the extent of source. 
    """

    def __init__(self, store, source):
        if not isinstance(source, RasterBlock):
            raise TypeError("'{}' object is not allowed".format(type(store)))
        super(Clip, self).__init__(store, source)

    @property
    def source(self):
        return self.args[1]

    @staticmethod
    def process(data, source_data):
        """ Mask store_data where source_data has no data """
        if data is None:
            return None

        if "values" not in data:
            return data

        # check if values contain data
        if np.all(data["values"] == data["no_data_value"]):
            return data

        # make the boolean mask
        if source_data is None:
            return None

        if source_data["values"].dtype == np.dtype("bool"):
            mask = ~source_data["values"]
        else:
            mask = source_data["values"] == source_data["no_data_value"]

        # adjust values
        values = data["values"].copy()
        values[mask] = data["no_data_value"]
        return {"values": values, "no_data_value": data["no_data_value"]}

    @property
    def extent(self):
        """Intersection of bounding boxes of 'store' and 'source'. """
        result, mask = [s.extent for s in self.args]
        if result is None or mask is None:
            return

        # return the overlapping box
        x1 = max(result[0], mask[0])
        y1 = max(result[1], mask[1])
        x2 = min(result[2], mask[2])
        y2 = min(result[3], mask[3])
        if x2 <= x1 or y2 <= y1:
            return None  # no overlap
        else:
            return x1, y1, x2, y2

    @property
    def geometry(self):
        """Intersection of geometries of 'store' and 'source'. """
        result, mask = [x.geometry for x in self.args]
        if result is None or mask is None:
            return
        sr = result.GetSpatialReference()
        if not mask.GetSpatialReference().IsSame(sr):
            mask = mask.Clone()
            mask.TransformTo(sr)
        result = result.Intersection(mask)
        if result.GetArea() == 0.0:
            return
        return result


class Mask(BaseSingle):
    """
    Convert 'data' values of a raster to a single constant value.
    
    Transforms all raster cells which carry a data value to a single, contant value.

    Args:
      store (RasterBlock): The raster whose values are to be converted.
      value (number): The constant value to be given to 'data' values.
    
    Returns:
      A RasterBlock containing a single value

    """

    def __init__(self, store, value):
        if not isinstance(value, (float, int)):
            raise TypeError("'{}' object is not allowed".format(type(value)))
        super(Mask, self).__init__(store, value)

    @property
    def value(self):
        return self.args[1]

    @property
    def fillvalue(self):
        return 1 if self.value == 0 else 0

    @property
    def dtype(self):
        return "float32" if isinstance(self.value, float) else "uint8"

    @staticmethod
    def process(data, value):
        if data is None or "values" not in data:
            return data

        index = get_index(values=data["values"], no_data_value=data["no_data_value"])

        fillvalue = 1 if value == 0 else 0
        dtype = "float32" if isinstance(value, float) else "uint8"

        values = np.full_like(data["values"], fillvalue, dtype=dtype)
        values[index] = value
        return {"values": values, "no_data_value": fillvalue}


class MaskBelow(BaseSingle):
    """
    Mask data below some value.

    Converts raster cells that are below the supplied value to no data. Raster cells with values equal to or higher than the supplied input value are returned unchanged. 
    
    Args:
      store (RasterBlock): The raster whose values are to be masked.
      value (number): The constant value below which values are masked.
    
    Returns:
      A RasterBlock where cells below the input value are converted to 'no data'
    """

    def __init__(self, store, value):
        if not isinstance(value, (float, int)):
            raise TypeError("'{}' object is not allowed".format(type(value)))
        super(MaskBelow, self).__init__(store, value)

    @staticmethod
    def process(data, value):
        if data is None or "values" not in data:
            return data
        values, no_data_value = data["values"].copy(), data["no_data_value"]
        values[values < value] = no_data_value
        return {"values": values, "no_data_value": no_data_value}


class Step(BaseSingle):
    """
    Classifies a raster by comparing cell values to a single value. 

    This geoblock allows you to supply a value and classify an input raster into three categories.
    One for cells below this value, one for cells equal to this value and one for cells higher than this value. 
    
    The step function is thus defined as follows, with x being the value of a raster cell
    
    - left if *x < value*
    
    - at if *x == value*
    
    - right if *x > value*

    Args:
      store (RasterBlock): The raster whose cell values are the input to the step function
      left (number): Value given to cells lower than the input value, defaults to 0
      right (number): Value given to cells higher than the input value, defaults to 1
      value (number): The constant value which raster cells are compared to, defaults to 0
      at (number): Value given to cells equal to the inport value, defaults to the average of left and right

    Returns:
      RasterBlock containing three values; left, right and at. 
    
    """

    def __init__(self, store, left=0, right=1, location=0, at=None):
        """Constructor.



        The at parameter defaults to the mean of the left and right values.
        """
        at = (left + right) / 2 if at is None else at
        for x in left, right, location, at:
            if not isinstance(x, (float, int)):
                raise TypeError("'{}' object is not allowed".format(type(x)))
        super(Step, self).__init__(store, left, right, location, at)

    @property
    def left(self):
        return self.args[1]

    @property
    def right(self):
        return self.args[2]

    @property
    def location(self):
        return self.args[3]

    @property
    def at(self):
        return self.args[4]

    @staticmethod
    def process(data, left, right, location, at):
        if data is None or "values" not in data:
            return data

        values, no_data_value = data["values"].copy(), data["no_data_value"]

        # determine boolean index arrays
        mask = values == no_data_value
        left_index = values < location
        at_index = values == location
        right_index = values > location
        # perform mapping
        values[left_index] = left
        values[at_index] = at
        values[right_index] = right
        # put no data values back
        values[mask] = no_data_value

        return {"values": values, "no_data_value": no_data_value}


class Classify(BaseSingle):
    """
    Classify raster data into binned categories

    Takes a RasterBlock and classifies its values based on bins. The bins are supplied as a list of ascending bin edges.
    For each raster cell this operation returns the index of the bin to which the raster cell belongs. The lowest possible output cell value is 0 (input value lower than lowest bin edge). 
    The highest possible output cell value is equal to the amount of supplied bin edges (input value higher than highest bin edge).

    Args:
      store (RasterBlock): The raster whose cell values are to be classified
      bins (list): An ascending list of bin edges 
      right (boolean): whether the intervals include the right or the left bin edge, defaults to the left bin edge
    
    Returns:
      RasterBlock with classified values

    See also:
      https://docs.scipy.org/doc/numpy/reference/generated/numpy.digitize.html
    """

    def __init__(self, store, bins, right=False):
        if not isinstance(store, RasterBlock):
            raise TypeError("'{}' object is not allowed".format(type(store)))
        if not hasattr(bins, "__iter__"):
            raise TypeError("'{}' object is not allowed".format(type(bins)))
        bins_arr = np.asarray(bins)
        if bins_arr.ndim != 1:
            raise TypeError("'bins' should be one-dimensional")
        if not np.issubdtype(bins_arr.dtype, np.number):
            raise TypeError("'bins' should be numeric")
        bins_diff = np.diff(bins)
        if not np.all(bins_diff > 0) or np.all(bins_diff < 0):
            raise TypeError("'bins' should be monotonic")
        super(Classify, self).__init__(store, bins_arr.tolist(), right)

    @property
    def bins(self):
        return self.args[1]

    @property
    def right(self):
        return self.args[2]

    @property
    def dtype(self):
        # with 254 bin edges, we have 255 bins, and we need 256 possible values
        # to include no_data
        return get_uint_dtype(len(self.bins) + 2)

    @property
    def fillvalue(self):
        return get_dtype_max(self.dtype)

    @staticmethod
    def process(data, bins, right):
        if data is None or "values" not in data:
            return data

        values = data["values"]
        dtype = get_uint_dtype(len(bins) + 2)
        fillvalue = get_dtype_max(dtype)

        result_values = np.digitize(values, bins, right).astype(dtype)
        result_values[values == data["no_data_value"]] = fillvalue

        return {"values": result_values, "no_data_value": fillvalue}


class Reclassify(BaseSingle):
    """
    Reclassify a raster of integers.
    
    This geoblocks can be used to reclassify a classified raster into desired values. Can for example be used to reclassify a raster resulting from ``dask_geomodeling.raster.misc.Classify``. 
    Reclassification is done by supplying a list of [from, to] pairs. In this pair from is the cell values of the input raster and to is the desired cell value of the output raster.
    
    Args:
      store (RasterBlock): The raster whose cell values are to be reclassified
      bins (list): A list of [from,to] pairs defining the reclassification.
        The from values can be of bool or int datatype; the to values can be of int or float datatype
      select (boolean): Selection to only keep reclassified values, and set all other cells to 'no data'. Defaults to False. 
    
    Returns:
      RasterBlock with reclassified values
    """

    def __init__(self, store, data, select=False):
        dtype = store.dtype
        if dtype != np.bool and not np.issubdtype(dtype, np.integer):
            raise TypeError("The store must be of boolean or integer datatype")

        # validate "data"
        if not hasattr(data, "__iter__"):
            raise TypeError("'{}' object is not allowed".format(type(data)))
        try:
            source, target = map(np.asarray, zip(*data))
        except ValueError:
            raise ValueError("Please supply a list of [from, to] values")
        # "from" can have bool or int dtype, "to" can also be float
        if source.dtype != np.bool and not np.issubdtype(source.dtype, np.integer):
            raise TypeError(
                "Cannot reclassify from value with type '{}'".format(source.dtype)
            )
        if len(np.unique(source)) != len(source):
            raise ValueError("There are duplicates in the reclassify values")
        if not np.issubdtype(target.dtype, np.number):
            raise TypeError(
                "Cannot reclassify to value with type '{}'".format(target.dtype)
            )
        # put 'data' into a list with consistent dtypes
        data = [list(x) for x in zip(source.tolist(), target.tolist())]

        if select is not True and select is not False:
            raise TypeError("'{}' object is not allowed".format(type(select)))
        super().__init__(store, data, select)

    @property
    def data(self):
        return self.args[1]

    @property
    def select(self):
        return self.args[2]

    @property
    def dtype(self):
        _, target = map(np.asarray, zip(*self.data))
        return target.dtype

    @property
    def fillvalue(self):
        return get_dtype_max(self.dtype)

    def get_sources_and_requests(self, **request):
        process_kwargs = {
            "dtype": self.dtype.str,
            "fillvalue": self.fillvalue,
            "data": self.data,
            "select": self.select,
        }
        return [(self.store, request), (process_kwargs, None)]

    @staticmethod
    def process(store_data, process_kwargs):
        if store_data is None or "values" not in store_data:
            return store_data

        no_data_value = store_data["no_data_value"]
        values = store_data["values"]
        source, target = map(np.asarray, zip(*process_kwargs["data"]))
        dtype = np.dtype(process_kwargs["dtype"])
        fillvalue = process_kwargs["fillvalue"]

        # add the nodata value to the source array and map it to the target
        # nodata
        if no_data_value is not None and no_data_value not in source:
            source = np.append(source, no_data_value)
            target = np.append(target, fillvalue)

        # sort the source and target values
        inds = np.argsort(source)
        source = source[inds]
        target = target[inds]

        # create the result array
        if process_kwargs["select"]:  # select = True: initialize with nodata
            result = np.full(values.shape, fillvalue, dtype=dtype)
        else:  # select = True: initialize with existing data
            result = values.astype(dtype)  # makes a copy

        # find all values in the source data that are to be mapped
        mask = np.in1d(values.ravel(), source)
        mask.shape = values.shape
        # place the target values (this also maps nodata values)
        result[mask] = target[np.searchsorted(source, values[mask])]
        return {"values": result, "no_data_value": fillvalue}


class Rasterize(RasterBlock):
    """
    Converts geometry source to raster

    This geoblock can be used to link geometry blocks to raster blocks. Here geometries (shapefiles) or converted to a raster, using the values from one of the columns. 
    To rasterize floating point values, it is necessary to pass dtype='float'.
    
    Args:
      source (GeometryBlock): The geometry to be rasterized
      column_name (string): The name of the column whose values will be returned in the raster. If column_name is not provided, a boolean raster will be returned indicating where there are geometries.
      dtype (string): a numpy datatype specification to return the array. Defaults to 'int32' if column_name is provided, else it defaults to 'bool'.
    
    Returns: 
      a raster containing values from 'column_name' or a boolean raster if 'column_name' is not provided. 

    See also:
      https://docs.scipy.org/doc/numpy/reference/arrays.dtypes.html

    The global geometry-limit setting can be adapted as follows:
      >>> from dask import config
      >>> config.set({"geomodeling.geometry-limit": 100000})
    """

    def __init__(self, source, column_name=None, dtype=None, limit=None):
        if not isinstance(source, GeometryBlock):
            raise TypeError("'{}' object is not allowed".format(type(source)))
        if column_name is not None and not isinstance(column_name, str):
            raise TypeError("'{}' object is not allowed".format(type(column_name)))
        if dtype is None:  # set default values
            dtype = "bool" if column_name is None else "int32"
        else:  # parse to numpy dtype and back to string
            dtype = str(np.dtype(dtype))
        if limit and not isinstance(limit, int):
            raise TypeError("'{}' object is not allowed".format(type(limit)))
        if limit and limit < 1:
            raise ValueError("Limit should be greater than 1")
        super(Rasterize, self).__init__(source, column_name, dtype, limit)

    @property
    def source(self):
        return self.args[0]

    @property
    def column_name(self):
        return self.args[1]

    @property
    def limit(self):
        return self.args[3]

    @property
    def dtype(self):
        return np.dtype(self.args[2])

    @property
    def fillvalue(self):
        return None if self.dtype == np.bool else get_dtype_max(self.dtype)

    @property
    def period(self):
        return (self.DEFAULT_ORIGIN,) * 2

    @property
    def extent(self):
        return None

    @property
    def timedelta(self):
        return None

    @property
    def geometry(self):
        return None

    @property
    def projection(self):
        return None

    @property
    def geo_transform(self):
        return None

    def get_sources_and_requests(self, **request):
        # first handle the 'time' and 'meta' requests
        mode = request["mode"]
        if mode == "time":
            return [(self.period[-1], None), ({"mode": "time"}, None)]
        elif mode == "meta":
            return [(None, None), ({"mode": "meta"}, None)]
        elif mode != "vals":
            raise ValueError("Unknown mode '{}'".format(mode))

        # build the request to be sent to the geometry source
        x1, y1, x2, y2 = request["bbox"]
        width, height = request["width"], request["height"]

        # be strict about the bbox, it may lead to segfaults else
        if x2 == x1 and y2 == y1:  # point
            min_size = None
        elif x1 < x2 and y1 < y2:
            min_size = min((x2 - x1) / width, (y2 - y1) / height)
        else:
            raise ValueError("Invalid bbox ({})".format(request["bbox"]))

        limit = self.limit
        if self.limit is None:
            limit = config.get("geomodeling.geometry-limit")

        geom_request = {
            "mode": "intersects",
            "geometry": shapely.geometry.box(*request["bbox"]),
            "projection": request["projection"],
            "min_size": min_size,
            "limit": limit,
            "start": request.get("start"),
            "stop": request.get("stop"),
        }
        # keep some variables for use in process()
        process_kwargs = {
            "mode": "vals",
            "column_name": self.column_name,
            "dtype": self.dtype,
            "no_data_value": self.fillvalue,
            "width": width,
            "height": height,
            "bbox": request["bbox"],
        }
        return [(self.source, geom_request), (process_kwargs, None)]

    @staticmethod
    def process(data, process_kwargs):
        # first handle the time and meta requests
        mode = process_kwargs["mode"]
        if mode == "time":
            return {"time": [data]}
        elif mode == "meta":
            return {"meta": [None]}

        column_name = process_kwargs["column_name"]
        height = process_kwargs["height"]
        width = process_kwargs["width"]
        no_data_value = process_kwargs["no_data_value"]
        dtype = process_kwargs["dtype"]
        f = data["features"]

        # get the value column to rasterize
        if column_name is None:
            values = None
        else:
            try:
                values = f[column_name]
            except KeyError:
                if f.index.name == column_name:
                    values = f.index.to_series()
                else:
                    values = False

        if len(f) == 0 or values is False:  # there is no data to rasterize
            values = np.full((1, height, width), no_data_value, dtype=dtype)
            return {"values": values, "no_data_value": no_data_value}

        result = rasterize_geoseries(
            geoseries=f["geometry"] if "geometry" in f else None,
            values=values,
            bbox=process_kwargs["bbox"],
            projection=data["projection"],
            height=height,
            width=width,
        )

        values = result["values"]

        # cast to the expected dtype if necessary
        cast_values = values.astype(process_kwargs["dtype"])

        # replace the nodata value if necessary
        if result["no_data_value"] != no_data_value:
            cast_values[values == result["no_data_value"]] = no_data_value

        return {"values": cast_values, "no_data_value": no_data_value}
