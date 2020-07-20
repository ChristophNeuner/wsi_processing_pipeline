# ------------------------------------------------------------------------
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# ------------------------------------------------------------------------

# To get around renderer issue on macOS going from Matplotlib image to NumPy image.
import matplotlib

matplotlib.use('Agg')

import PIL
import pathlib
from pathlib import Path
import colorsys
import math
import matplotlib.pyplot as plt
import multiprocessing
import numpy
import numpy as np
import os
import PIL
from PIL import Image, ImageDraw, ImageFont
from enum import Enum
import openslide
import multiprocessing
from typing import List, Callable, Union, Dict, Tuple, Union
from tqdm import tqdm
import pandas
import pandas as pd
import warnings
from enum import Enum
import copy

import sys
sys.path.append("../")
from tile_extraction import util, filter, slide, openslide_overwrite
from tile_extraction.util import *


TISSUE_HIGH_THRESH = 80
TISSUE_LOW_THRESH = 10
HSV_PURPLE = 270
HSV_PINK = 330

############################# classes #########################################


class DatasetType(Enum):
    train = 0
    validation = 1
    test = 2


class RegionOfInterest:
    """
    represents a region of interest within a whole-slide image
    """
    x_upper_left:int = None
    y_upper_left:int = None
    height:int = None
    width:int = None
    level:int = None
    name:str = None

    def __init__(self, 
                 x_upper_left:int, 
                 y_upper_left:int, 
                 height:int, 
                 width:int, 
                 level:int, 
                 name:str = None):
        """
            Arguments:
            x_upper_left: x coordinate of roi's upper left point
            y_upper_left: y coordinate of roi's upper left point
            height: roi's height in pixel
            width: roi's width in pixel
            level: level of the whole-slide image. 0 means highest resolution. Leave it 0 if you use e.g. png files instead of a 
                    whole-slide image format like .ndpi
            name: a name for the roi 
        """
        self.x_upper_left = x_upper_left
        self.y_upper_left = y_upper_left
        self.height = height
        self.width = width
        self.level = level
        self.name = name
        
    def __repr__(self):
        return f"x: {self.x_upper_left}, y: {self.y_upper_left}, height: {self.height}, width: {self.width}, level: {self.level}"
        
    def change_level_in_place(self, new_level:int):
        """
        adjusts all properties to new level in place and also returns itself
        """
        self.x_upper_left = util.adjust_level(value_to_adjust=self.x_upper_left, from_level=self.level, to_level=new_level)
        self.y_upper_left = util.adjust_level(value_to_adjust=self.y_upper_left, from_level=self.level, to_level=new_level)
        self.height = util.adjust_level(value_to_adjust=self.height, from_level=self.level, to_level=new_level)
        self.width = util.adjust_level(value_to_adjust=self.width, from_level=self.level, to_level=new_level)
        self.level = new_level
        return self
    
    def change_level_deep_copy(self, new_level:int):
        """
        returns deep copy of itself with adjusted properties
        """
        dc = copy.deepcopy(self)
        dc.x_upper_left = util.adjust_level(value_to_adjust=self.x_upper_left, from_level=self.level, to_level=new_level)
        dc.y_upper_left = util.adjust_level(value_to_adjust=self.y_upper_left, from_level=self.level, to_level=new_level)
        dc.height = util.adjust_level(value_to_adjust=self.height, from_level=self.level, to_level=new_level)
        dc.width = util.adjust_level(value_to_adjust=self.width, from_level=self.level, to_level=new_level)
        dc.level = new_level
        return dc

class WsiInfo:
    path:pathlib.Path = None
    patient_id = None
    case_id = None
    slide_id = None
    classification_labels = None
    dataset_type = None
    rois:List[RegionOfInterest] = None

    def __init__(self, 
                 path:Union[str,pathlib.Path], 
                 patient_id:str, case_id:str, 
                 slide_id:str, 
                 classification_labels:List[str], 
                 dataset_type:DatasetType, 
                 rois:List[RegionOfInterest]):
        self.path = pathlib.Path(path)
        self.patient_id = patient_id
        self.case_id = case_id
        self.slide_id = slide_id
        self.classification_labels = classification_labels
        self.dataset_type = dataset_type
        self.rois = rois
        
    def change_level_of_rois(self, new_level:int):
        """
            convenience function to change the level for all rois at once in place
        """
        for roi in self.rois:
            if roi != None:
                roi.change_level_in_place(new_level)


class TileSummary:
    """
    Class for tile summary information.
    """

    wsi_path = None
    tiles_folder_path = None    
    orig_w = None #full width in pixels of the wsi on the specified level
    orig_h = None #full height in pixels of the wsi on the specified level
    orig_tile_w = None 
    orig_tile_h = None
    
    minimal_acceptable_tile_width = None #between 0.0 and 1.0; percentage of orig_w ;tiles at the edges will not have full width
    minimal_acceptable_tile_height = None #between 0.0 and 1.0; percentage of orig_h ;tiles at the edges will not have full height
    
    scale_factor = None #for faster processing the wsi is scaled down internally, the resulting tiles are on maximum resolution
                                #depending on the specified level
    scaled_w = None 
    scaled_h = None
    scaled_tile_w = None
    scaled_tile_h = None
    mask_percentage = None
    num_row_tiles = None
    num_col_tiles = None
    tile_score_thresh = None
    level = None
    best_level_for_downsample = None
    real_scale_factor = None
        
    wsi_info = None
    
    tiles = None

    count = 0
    high = 0
    medium = 0
    low = 0
    none = 0

    def __init__(self, 
                 wsi_path,
                 tiles_folder_path, 
                 orig_w, 
                 orig_h, 
                 orig_tile_w, 
                 orig_tile_h,
                 minimal_acceptable_tile_width,
                 minimal_acceptable_tile_height,
                 scale_factor, 
                 scaled_w, 
                 scaled_h, 
                 scaled_tile_w,
                 scaled_tile_h, 
                 tissue_percentage, 
                 num_col_tiles, 
                 num_row_tiles, 
                 tile_score_thresh, 
                 level, 
                 best_level_for_downsample,
                 real_scale_factor, 
                 wsi_info:WsiInfo=None):

        """
        Arguments:
            level: whole-slide image's level, the tiles shall be extracted from
            orig_w, orig_h: original height and original width depend on the specified level. With each level, the dimensions half.
            
            minimal_acceptable_tile_width: factor between 0.0 and 1.0. percentage of orig_w ;affects tiles at the edges, which cannot 
                                            have full width
            minimal_acceptable_tile_height: factor between 0.0 and 1.0. percentage of orig_h ;affects tiles at the edges, which 
                                            cannot have full height                                                               
                                                                       
            scale_factor:  Downscaling is applied during tile calculation to speed up the process. The tiles in the end get extracted 
                            from the full resolution. the full resolution depends on the level, the user specifies. The higher the 
                            level, the lower the resolution/magnification.
                                 Therefore less downsampling needs to be applied during tile calculation, to achieve same speed up.
                                 So e.g. the wsi has dimensions of 10000x10000 pixels on level 0. A scale_factor of 32 is speficied. 
                                 Then calculations will be applied on
                                 a downscaled version of the wsi with dimensions on the level log2(32)

            real_scale_factor: if a scale_factor of e.g. 32 is specified and a level of 0, from which the tiles shall be extracted, 
                                scale_factor==real_scale_factor.
                                 For each level, the wsi dimensions half.
                                 That means for a scale_factor of 32 and level 1 the real_scale_factor would be only 16.
                                 downscaling is applied during tile calculation to speed up the process. The tile in the end get 
                                 extracted from the full resolution
                                 The full resolution depends on the level, the user specifies. The higher the level, the lower the 
                                 resolution/magnification.
                                 Therefore less downsampling needs to be applied during tile calculation, to achieve same speed up.

            best_level_for_downsample: result of openslide.OpenSlide.get_best_level_for_downsample(scale_factor)
        """

        self.wsi_path = wsi_path
        self.tiles_folder_path = tiles_folder_path
        self.orig_w = orig_w
        self.orig_h = orig_h
        self.orig_tile_w = orig_tile_w
        self.orig_tile_h = orig_tile_h       
        self.minimal_acceptable_tile_width = minimal_acceptable_tile_width
        self.minimal_acceptable_tile_height = minimal_acceptable_tile_height        
        self.scale_factor = scale_factor
        self.scaled_w = scaled_w
        self.scaled_h = scaled_h
        self.scaled_tile_w = scaled_tile_w
        self.scaled_tile_h = scaled_tile_h
        self.tissue_percentage = tissue_percentage
        self.num_col_tiles = num_col_tiles
        self.num_row_tiles = num_row_tiles
        self.tile_score_thresh = tile_score_thresh
        self.level = level
        self.best_level_for_downsample = best_level_for_downsample
        self.real_scale_factor = real_scale_factor
        
        self.tiles = []
        
        if wsi_info != None:
            self.set_wsi_info(wsi_info)
        
        

    #def __str__(self):
    #    return summary_title(self) + "\n" + summary_stats(self)

    def mask_percentage(self):
        """
        Obtain the percentage of the slide that is masked.

        Returns:
           The amount of the slide that is masked as a percentage.
        """
        return 100 - self.tissue_percentage

    def num_tiles(self):
        """
        Retrieve the total number of tiles.

        Returns:
          The total number of tiles (number of rows * number of columns).
        """
        return self.num_row_tiles * self.num_col_tiles

    def tiles_by_tissue_percentage(self):
        """
        Retrieve the tiles ranked by tissue percentage.

        Returns:
           List of the tiles ranked by tissue percentage.
        """
        sorted_list = sorted(self.tiles, key=lambda t: t.tissue_percentage, reverse=True)
        return sorted_list

    def tiles_by_score(self):
        """
        Retrieve the tiles ranked by score. If rois were specified, only tiles within those rois will be taken into account.

        Returns:
           List of the tiles ranked by score.
        """
        sorted_list = sorted(self.tiles, key=lambda t: t.score, reverse=True)
        return sorted_list

    def get_tile(self, row, col):
        """
        Retrieve tile by row and column.

        Args:
          row: The row
          col: The column

        Returns:
          Corresponding Tile object.
        """
        tile_index = (row - 1) * self.num_col_tiles + (col - 1)
        tile = self.tiles[tile_index]
        return tile
    
    def top_tiles(self, verbose=False):
        """
        Retrieve only the tiles that pass scoring and their height and width are greater than 
        minimal_acceptable_tile_height/minimal_acceptable_tile_width.

        Returns:
           List of the top-scoring tiles.
        """
        sorted_tiles = self.tiles_by_score()
        top_tiles = [tile for tile in sorted_tiles
                     if self.check_tile(tile)]
        if verbose:
            print(f'{self.wsi_path}: Number of tiles that will be kept/all possible tiles: {len(top_tiles)}/{len(sorted_tiles)}')
        return top_tiles

    def check_tile(self, tile):
        width = tile.o_c_e - tile.o_c_s
        height = tile.o_r_e - tile.o_r_s
        return tile.score > self.tile_score_thresh and width >= self.minimal_acceptable_tile_width*self.orig_tile_w and height >= self.minimal_acceptable_tile_height*self.orig_tile_h
        
    def set_wsi_info(self, wsi_info:WsiInfo):
        """
         convenience function to set all parameters in WsiInfo at once and also for all tiles
        """
        self.wsi_info = wsi_info
        self.set_patient_id(wsi_info.patient_id)
        self.set_case_id(wsi_info.case_id)
        self.set_slide_id(wsi_info.slide_id)
        self.set_classification_labels(wsi_info.classification_labels)
        self.set_dataset_type(wsi_info.dataset_type)

    def set_patient_id(self, patient_id:str):
        """
        sets patient_id for all its tiles as well
        """
        self.wsi_info.patient_id = patient_id
        for t in self.tiles:
            t.set_patient_id(patient_id)

    def set_case_id(self, case_id:str):
        """
        sets case_id for all its tiles as well
        """
        self.wsi_info.case_id = case_id
        for t in self.tiles:
            t.set_case_id(case_id)

    def set_slide_id(self, slide_id:str):
        """
        sets slide_id for all its tiles as well
        """
        self.wsi_info.slide_id = slide_id
        for t in self.tiles:
            t.set_slide_id(slide_id)

    def set_classification_labels(self, classification_labels:List):
        """
        sets classification_labels for all its tiles as well
        """
        self.wsi_info.classification_labels = classification_labels
        for t in self.tiles:
            t.set_classification_labels(classification_labels)

    def set_dataset_type(self, dataset_type:DatasetType):
        """
        sets dataset_type for all its tiles as well
        """
        self.wsi_info.dataset_type = dataset_type
        for t in self.tiles:
            t.set_dataset_type(dataset_type)      

    def show_wsi_with_marked_tiles(self, 
                                   figsize:Tuple[int] = (10,10),
                                   scale_factor:int = 32):
        """    
        Loads a whole slide image, scales it down, converts it into a numpy array and displays it with a grid overlay for all tiles
        that passed scoring to visualize which tiles e.g. "tiles.WsiOrROIToTilesMultithreaded" calculated as worthy to keep.
        Arguments:
            figsize: Size of the plotted matplotlib figure containing the image.
            scale_factor: The larger, the faster this method works, but the plotted image has less resolution.    
        """
        wsi_pil, large_w, large_h, new_w, new_h, best_level_for_downsample = wsi_to_scaled_pil_image(self.wsi_path,
                                                                                                        scale_factor=self.scale_factor,
                                                                                                        level=0)
        wsi_np = util.pil_to_np_rgb(wsi_pil)
        boxes =[]
        
        for tile in self.top_tiles():
            x = util.adjust_level(tile.get_x(), tile.level, best_level_for_downsample)
            y = util.adjust_level(tile.get_y(), tile.level, best_level_for_downsample)
            width = util.adjust_level(tile.get_width(), tile.level, best_level_for_downsample)
            height = util.adjust_level(tile.get_height(), tile.level, best_level_for_downsample)
            box = np.array([x,y,width,height])
            boxes.append(box)
        util.show_np_with_bboxes(wsi_np, boxes, figsize)
        
        
    def show_wsi_with_rois(self, 
                           figsize:Tuple[int] = (10,10),
                           scale_factor:int = 32):
        """    
        Loads a whole slide image, scales it down, converts it into a numpy array and displays it with a grid overlay for all rois
        specified in self.wsi_info.rois
        Arguments:
            figsize: Size of the plotted matplotlib figure containing the image.
            scale_factor: The larger, the faster this method works, but the plotted image has less resolution.    
        """
        util.show_wsi_with_rois(self.wsi_path, self.wsi_info.rois)
        
      
class Tile:
    """
    Class for information about a tile.
    """
    tile_summary = None
    wsi_path = None
    tiles_folder_path = None
    np_scaled_filtered_tile = None
    tile_num = None
    r = None # (=row) e.g. the wsi has a height of 1024 pixel and one tile has a height of 256 pixels, r can be in range [1,4] (ends 
             # included); if rois are specified, it's the row number inside the roi; so tiles from different rois can have the same
             # r and c value but with respect to different rois
    c = None # (=column) like r but according to width
    
    r_s = None #(=row_start)pixel value on y-axis of the SCALED down wsi; always with respect to the wsi nevertheless rois are 
                # specified
    r_e = None # (=row_end)
    c_s = None # (=column_start) like r_s but x-axis
    c_e = None #(=column_end)
    
    o_r_s = None #(=original_row_start)pixel value on y-axis of the UNscaled wsi on the specified level; always with respect to the 
                    # wsi nevertheless rois are specified
    o_r_e = None #(=original_row_end)
    o_c_s = None #(=original_column_start)
    o_c_e = None #(=original_column_end)
    
    t_p = None #tissue percentage
    color_factor = None
    s_and_v_factor = None
    quantity_factor = None
    score = None
    tile_naming_func = None
    level = None
    best_level_for_downsample = None
    real_scale_factor = None
    patient_id = None
    case_id = None
    slide_id = None
    classification_labels:List = None
    dataset_type:DatasetType = None
    roi:RegionOfInterest = None
                
    def __init__(self, 
                 tile_summary, 
                 wsi_path,
                 tiles_folder_path, 
                 np_scaled_filtered_tile, 
                 tile_num, 
                 r, 
                 c, 
                 r_s, 
                 r_e, 
                 c_s, 
                 c_e, 
                 o_r_s, 
                 o_r_e, 
                 o_c_s,
                 o_c_e, 
                 t_p, 
                 color_factor, 
                 s_and_v_factor, 
                 quantity_factor, 
                 score, 
                 tile_naming_func, 
                 level,
                 best_level_for_downsample,
                 real_scale_factor,
                 roi:RegionOfInterest,
                 patient_id = None, 
                 case_id = None, 
                 slide_id = None, 
                 classification_labels:List = None, 
                 dataset_type:DatasetType = None):
        """
        Arguments:
            level: whole-slide image's level, the tile shall be extracted from
            best_level_for_downsample: openslide.OpenSlide.get_best_level_for_downsample(scale_factor)
            best_level_for_downsample: openslide.OpenSlide.get_best_level_for_downsample(scale_factor)
        """


        self.tile_summary = tile_summary
        self.wsi_path = wsi_path
        self.roi = roi
        self.tiles_folder_path = tiles_folder_path
        self.np_scaled_filtered_tile = np_scaled_filtered_tile
        self.tile_num = tile_num
        self.r = r
        self.c = c
        self.r_s = r_s
        self.r_e = r_e
        self.c_s = c_s
        self.c_e = c_e
        self.o_r_s = o_r_s
        self.o_r_e = o_r_e
        self.o_c_s = o_c_s
        self.o_c_e = o_c_e
        self.tissue_percentage = t_p
        self.color_factor = color_factor
        self.s_and_v_factor = s_and_v_factor
        self.quantity_factor = quantity_factor
        self.score = score
        self.tile_naming_func = tile_naming_func
        self.level = level
        self.best_level_for_downsample = best_level_for_downsample
        self.roi = roi
        self.real_scale_factor = real_scale_factor
        self.patient_id = patient_id
        self.case_id = case_id
        self.slide_id = slide_id
        self.classification_labels = classification_labels
        self.dataset_type = dataset_type

    def __str__(self):
        return "[Tile #%d, Row #%d, Column #%d, Tissue %4.2f%%, Score %0.4f]" % (
          self.tile_num, self.r, self.c, self.tissue_percentage, self.score)

    def __repr__(self):
        return "\n" + self.__str__()

    def mask_percentage(self):
        return 100 - self.tissue_percentage

    def tissue_quantity(self):
        return tissue_quantity(self.tissue_percentage)

    def get_pil_tile(self):
        return tile_to_pil_tile(self)

    def get_np_tile(self):
        return tile_to_np_tile(self)

    def save_tile(self):
        save_display_tile(self, save=True, display=Falsei)

    def display_tile(self):
        save_display_tile(self, save=False, display=True)

    def display_with_histograms(self):
        display_tile(self, rgb_histograms=True, hsv_histograms=True)

    def get_np_scaled_filtered_tile(self):
        return self.np_scaled_filtered_tile

    def get_pil_scaled_filtered_tile(self):
        return util.np_to_pil(self.np_scaled_filtered_tile)
    
    def get_width(self):
        return self.o_c_e - self.o_c_s
    
    def get_height(self):
        return self.o_r_e - self.o_r_s
    
    def get_x(self):
        """
        upper left x coordinate
        """
        return self.o_c_s
    
    def get_y(self):
        """
        upper left y coordinate
        """
        return self.o_r_s
    
    def get_path(self)->pathlib.Path:
        return pathlib.Path(get_tile_image_path(self))
                  
    def get_name(self)->str:
        return pathlib.Path(get_tile_image_path(self)).name

    def set_wsi_info(self, wsi_info:WsiInfo):
        """
         convenience function to set all parameters in WsiInfo at once
        """
        self.set_patient_id(wsi_info.patient_id)
        self.set_case_id(wsi_info.case_id)
        self.set_slide_id(wsi_info.slide_id)
        self.set_classification_labels(wsi_info.classification_labels)
        self.set_dataset_type(wsi_info.dataset_type)

    def set_patient_id(self, patient_id:str):
        self.patient_id = patient_id

    def set_case_id(self, case_id:str):
        self.case_id = case_id

    def set_slide_id(self, slide_id:str):
        self.slide_id = slide_id

    def set_classification_labels(self, classification_labels:List):
        self.classification_labels = classification_labels

    def set_dataset_type(self, dataset_type:DatasetType):
        self.dataset_type = dataset_type
        
      

class TissueQuantity(Enum):
  NONE = 0
  LOW = 1
  MEDIUM = 2
  HIGH = 3



############################# functions #########################################

###
# some example implementations for functions that other methods below take as arguments
###
def scoring_function_1(tissue_percent, combined_factor):
    """
    use this, if you want tissue with lots of cells (lots of hematoxylin stained tissue)
    """
    return tissue_percent * combined_factor / 1000.0

def scoring_function_2(tissue_percent, combined_factor):
    """
    use this, if you mostly care that there is any tissue in the tile
    """
    return (tissue_percent ** 2) * np.log(1 + combined_factor) / 1000.0

def get_roi_name_from_path_pituitary_adenoma_entities(roi_path):
    path = Path(roi_path)
    split = path.stem.split('-')
    if split[2] == 'HE':
        return f'{split[0]}-{split[1]}-{split[2]}-{split[3]}-{split[4]}'
    else:
        return f'{split[0]}-{split[1]}-{split[2]}-{split[3]}-{split[4]}-{split[5]}'

def get_wsi_name_from_path_pituitary_adenoma_entities(wsi_path):
    path = Path(wsi_path)
    split = path.stem.split('-')
    return f'{split[0]}-{split[1]}-{split[2]}-{split[3]}'

def tile_naming_function_default(wsi_or_roi_path:Union[pathlib.Path, str])-> str:
    """
    Used as default in WsiOrROIToTiles and WsiOrROIToTilesMultithreaded
    See their docu for more info
    """
    p = Path(wsi_or_roi_path)
    return p.stem

###
# end
###


def ExtractTileFromWSI(path:Union[str, pathlib.Path], x:int, y:int, width:int, height:int, level:int)-> PIL.Image:
    """
    Args:
        path: path to wsi
        x: x-coordinate of the upper left pixel. The method assumes, that you know the dimensions of your specified level.
        y: y-coordinate of the upper left pixel. The method assumes, that you know the dimensions of your specified level.
        width: tile width
        height: tile height
        level: Level of the WSI you want to extract the tile from. 0 means highest resolution.
        
    Return:
        tile as PIL.Image as RGB
    """
    s = slide.open_slide(str(path))
    tile_region = s.read_region((x, y), level, (width, height))
    # RGBA to RGB
    pil_img = tile_region.convert("RGB")
    return pil_img

def ExtractTileFromPILImage(path:Union[str, pathlib.Path], x:int, y:int, width:int, height:int)-> PIL.Image:
    """
    Args:
        path: path to PIL Image
        x: x-coordinate of the upper left pixel
        y: y-coordinate of the upper left pixel
        width: tile width
        height: tile height
        
    Return:
        tile as PIL.Image as RGB
    """
    #pil_img = PIL.Image.open(path)
    #pil_img = pil_img.crop((x, y, x+width, y+height))
    #return pil_img
    return ExtractTileFromWSI(path=path, x=x, y=y, width=width, height=height, level=0);


def WsiOrROIToTiles(wsi_path:pathlib.Path, 
               tiles_folder_path:pathlib.Path,
               tile_height:int, 
               tile_width:int,
               minimal_acceptable_tile_height:float = 0.7,
               minimal_acceptable_tile_width:float = 0.7,
               tile_naming_func:Callable = tile_naming_function_default,
               tile_score_thresh:float = 0.55,
               tile_scoring_function = scoring_function_1,
               level = 0, 
               save_tiles:bool = False, 
               return_as_tilesummary_object = False, 
               wsi_info:WsiInfo = None, 
               verbose=False)-> Union[TileSummary, pandas.DataFrame]:
    """
    Calculates tile coordinates and returns a TileSummary object. If save_tiles == True the tiles will also be extracted
    and saved from the WSI or ROI (ROI is assumed to be a "normal" image format like .png).
    
    Arguments:
    wsi_path: Path to a WSI or ROI(=already extracted part of a wsi in e.g. .png format)
    tiles_folder_path: The folder where the extracted tiles will be saved (only needed if save_tiles=True).
    tile_heigth: Number of pixels tile height.
    tile_width: Number of pixels tile width.
    minimal_acceptable_tile_height: factor between 0.0 and 1.0. percentage of orig_h ;affects tiles at the edges, which 
                                    cannot have full height 
    minimal_acceptable_tile_width: factor between 0.0 and 1.0. percentage of orig_w ;affects tiles at the edges, which cannot 
                                    have full width
    
    tile_score_thresh: Tiles with a score higher than the number from "tileScoringFunction" will be saved.
    tile_scoring_function: Function to score one tile to determine if it should be saved or not.
    tile_naming_func: 99% of the time there should be no necessity to change this.
                        A function, that takes a pathlib.Path to the WSI or ROI as an argument and returns a string.
                        This string will then be used as part of the name for the tile (plus some specific tile information and
                        the file format .png, whick is generated by this library).
                        
    level: Level of the WSI you want to extract the tiles from. 0 means highest resolution. For not wsi formats like .png leave it at 
            0.
    save_tiles: if True the tiles will be extracted and saved to {tilesFolderPath}
    return_as_tilesummary_object: return_as_tilesummary_object: Set this to true, if you 
                                    want the TileSummary object and not a pandas dataframe.
    wsi_info: not mandatory; a WsiInfo object. If you specify this, its information will also be set for all Tile objects in the 
                resulting TileSummary object.
    Return:
    if return_as_tilesummary_object == True:
       a TileSummary object will be returned
    else:
        pandas dataframe with coloumns: ['tile_name','wsi_path','level','x_upper_left','y_upper_left','pixels_width','pixels_height']
    """    

    if(tiles_folder_path is None and save_tiles == True):
        raise ValueError("You should specify a {tiles_folder_path}")
    
    if verbose:
        print(f"Starting to process {str(wsi_path)}")

    scale_factor = 32

    ### against DecompressionBombWarning
    #mage.MAX_IMAGE_PIXELS = 10000000000000
    openslide.lowlevel._load_image = openslide_overwrite._load_image

    img_pil, original_width, original_height, scaled_width, scaled_height, best_level_for_downsample = wsi_to_scaled_pil_image(wsi_path, scale_factor, level)

                
    img_pil_filtered = filter.filter_img(img_pil)
    tilesummary = create_tilesummary(wsiPath=wsi_path,
                                     tilesFolderPath=tiles_folder_path, 
                                     img_pil=img_pil, 
                                     img_pil_filtered=img_pil_filtered, 
                                     wsi_original_width=original_width, 
                                     wsi_original_height=original_height, 
                                     wsi_scaled_width=scaled_width, 
                                     wsi_scaled_height=scaled_height, 
                                     tile_height=tile_height, 
                                     tile_width=tile_width,
                                     minimal_acceptable_tile_height=minimal_acceptable_tile_height,
                                     minimal_acceptable_tile_width=minimal_acceptable_tile_width,
                                     scale_factor=scale_factor,
                                     tile_score_thresh=tile_score_thresh,
                                     tile_scoring_function=tile_scoring_function,
                                     tile_naming_func=tile_naming_func, 
                                     level=level, 
                                     best_level_for_downsample=best_level_for_downsample, 
                                     wsi_info=wsi_info, 
                                     verbose=verbose)
    
    if(save_tiles):
        for tile in tilesummary.top_tiles(verbose):
            tile.save_tile()
            
    if return_as_tilesummary_object:
        if verbose:
            tilesummary.top_tiles(verbose)
        return tilesummary
    
    else:    
        rows_list = []
        for tile in tilesummary.top_tiles(verbose):                                      
            row = {'tile_name':tile.get_name(),
                'wsi_path':tile.wsi_path,
                'level':tile.level,
                'x_upper_left':tile.get_x(),
                'y_upper_left':tile.get_y(),
                'pixels_width':tile.get_width(),
                'pixels_height':tile.get_height()}
            rows_list.append(row)
        
        if(len(rows_list) == 0):
            return pd.DataFrame(columns=['tile_name','wsi_path', \
                                         'level','x_upper_left','y_upper_left','pixels_width','pixels_height'])
        else:
            return pd.DataFrame(rows_list).set_index('tile_name', inplace=False)
        
        
def WsiOrROIToTilesMultithreaded(wsi_paths:List[pathlib.Path], 
                             tiles_folder_path:pathlib.Path,
                             tile_height:int, 
                             tile_width:int,
                             minimal_acceptable_tile_height:float = 0.7,
                             minimal_acceptable_tile_width:float = 0.7,
                             tile_naming_func:Callable = tile_naming_function_default,
                             tile_score_thresh:float = 0.55,
                             tile_scoring_function = scoring_function_1,  
                             level = 0, 
                             save_tiles:bool = False, 
                             return_as_tilesummary_object = False, 
                             wsi_path_to_wsi_info:Dict = None, 
                             verbose=False)-> Union[List[TileSummary], pandas.DataFrame]:
    """
    The method WsiOrROIToTiles for a list of WSIs/ROIs in parallel on multiple threads.
    
    Arguments:
    wsi_paths: A list of paths to the WSIs or ROIs
    tiles_folder_path: The folder where the extracted tiles will be saved (only needed if save_tiles=True).
    tile_heigth: Number of pixels tile height.
    tile_width: Number of pixels tile width.
    minimal_acceptable_tile_height: factor between 0.0 and 1.0. percentage of orig_h ;affects tiles at the edges, which 
                                    cannot have full height 
    minimal_acceptable_tile_width: factor between 0.0 and 1.0. percentage of orig_w ;affects tiles at the edges, which cannot 
                                    have full width
    tile_score_thresh: Tiles with a score higher than the number from "tileScoringFunction" will be saved.
    tile_scoring_function: Function to score one tile to determine if it should be saved or not.
    tile_naming_func: 99% of the time there should be no necessity to change this.
                        A function, that takes a pathlib.Path to the WSI or ROI as an argument and returns a string.
                        This string will then be used as part of the name for the tile (plus some specific tile information and
                        the file format .png, whick is generated by this library).
    level: Level of the WSI you want to extract the tile from. 0 means highest resolution. For not wsi formats like .png leave it at 
            0.
    save_tiles: if True the tiles will be extracted and saved to {tilesFolderPath}
    return_as_tilesummary_object: Set this to true, if you want the TileSummary object and not a pandas dataframe.
    wsi_path_to_wsi_info: a dict with key: wsi_path and value WsiInfo object. If you set this, the info will we passed down to all 
                            tiles in the tilesummary result
    Return:
    if return_as_tilesummary_object == True:
       a List of TileSummary objects will be returned
    else:
        pandas dataframe with coloumns: ['tile_name','wsi_path','level','x_upper_left','y_upper_left','pixels_width','pixels_height']
    """
    
    pbar = tqdm(total=len(wsi_paths))
    results = []
    def update(res):
        results.append(res)
        pbar.update()
        
    def error(e):
        print(e)
    
    with multiprocessing.Pool() as pool:
        for p in wsi_paths:
            pool.apply_async(WsiOrROIToTiles, 
                             kwds={"wsi_path":p, 
                                   "tiles_folder_path":tiles_folder_path,
                                   "tile_height":tile_height, 
                                   "tile_width":tile_width,
                                   "minimal_acceptable_tile_height":minimal_acceptable_tile_height,
                                   "minimal_acceptable_tile_width":minimal_acceptable_tile_width,
                                   "tile_naming_func":tile_naming_func,
                                   "tile_score_thresh":tile_score_thresh, 
                                   "tile_scoring_function":tile_scoring_function, 
                                   "level":level, 
                                   "save_tiles":save_tiles, 
                                   "return_as_tilesummary_object":return_as_tilesummary_object, 
                                   "wsi_info":util.safe_dict_access(wsi_path_to_wsi_info, p), 
                                   "verbose":verbose}, 
                                   callback=update, 
                                   error_callback=error)
            
                
        pool.close()
        pool.join()
    
    if return_as_tilesummary_object:
        return results
    else:
        merged_df = None
        for res in tqdm(results):
            if merged_df is None:
                merged_df = res
            else:
                merged_df = merged_df.append(res, sort=False)
        
        return merged_df.drop_duplicates(inplace=False)
        

       
def wsi_to_scaled_pil_image(wsi_filepath:pathlib.Path, scale_factor = 32, level = 0):
    """
    Convert a WSI training slide to a PIL image.

    Args:

    Returns:

    """
    #wsi = openslide.open_slide(str(wsi_filepath))
    #large_w, large_h = wsi.dimensions
    #new_w = math.floor(large_w / scale_factor)
    #new_h = math.floor(large_h / scale_factor)
    #level = wsi.get_best_level_for_downsample(scale_factor)
    #img = wsi.read_region((0, 0), level, wsi.level_dimensions[level])
    #img = img.convert("RGB")
    #if(scale_factor > 1):
    #    img = img.resize((new_w, new_h), PIL.Image.BILINEAR)
    #return img, large_w, large_h, new_w, new_h

    wsi = openslide.open_slide(str(wsi_filepath))
    large_w, large_h = wsi.level_dimensions[level]
    best_level_for_downsample = wsi.get_best_level_for_downsample(scale_factor)
    new_w, new_h = wsi.level_dimensions[best_level_for_downsample]    
    img = wsi.read_region((0, 0), best_level_for_downsample, wsi.level_dimensions[best_level_for_downsample])
    img = img.convert("RGB")
    return img, large_w, large_h, new_w, new_h, best_level_for_downsample


def create_tilesummary(wsiPath,
                        tilesFolderPath,
                        img_pil:PIL.Image.Image, 
                        img_pil_filtered:PIL.Image.Image, 
                        wsi_original_width:int, 
                        wsi_original_height:int, 
                        wsi_scaled_width:int, 
                        wsi_scaled_height:int, 
                        tile_height:int, 
                        tile_width:int,
                        minimal_acceptable_tile_height:float,
                        minimal_acceptable_tile_width:float,
                        scale_factor:int,
                        tile_score_thresh:float,
                        tile_scoring_function, 
                        tile_naming_func, 
                        level:int, 
                        best_level_for_downsample:int, 
                        wsi_info:WsiInfo = None, 
                        verbose=False)->TileSummary:
    """
  
    Args:
        wsi_original_width: unscaled width with respect to the specified level
        wsi_original_height: unscaled width with respect to the specified level
        scale_factor: the scale_factor specified by the user
        best_level_for_downsample: result of openslide.OpenSlide.get_best_level_for_downsample(scale_factor)
    """

    np_img = util.pil_to_np_rgb(img_pil)
    np_img_filtered = util.pil_to_np_rgb(img_pil_filtered)

    tile_sum = score_tiles(img_np=np_img, 
                           img_np_filtered=np_img_filtered, 
                           wsi_path=wsiPath,
                           tilesFolderPath=tilesFolderPath,
                           tile_height=tile_height,
                           tile_width=tile_width,
                           minimal_acceptable_tile_height=minimal_acceptable_tile_height,
                           minimal_acceptable_tile_width=minimal_acceptable_tile_width,
                           scale_factor=scale_factor, 
                           wsi_original_width=wsi_original_width, 
                           wsi_original_height=wsi_original_height, 
                           wsi_scaled_width=wsi_scaled_width, 
                           wsi_scaled_height=wsi_scaled_height,
                           tile_score_thresh=tile_score_thresh,
                           tile_scoring_function=tile_scoring_function, 
                           tile_naming_func=tile_naming_func, 
                           level=level, 
                           best_level_for_downsample=best_level_for_downsample, 
                           wsi_info=wsi_info, 
                           verbose=verbose)    
    return tile_sum


def get_num_tiles(rows, cols, row_tile_size, col_tile_size):
  """
  Obtain the number of vertical and horizontal tiles that an image can be divided into given a row tile size and
  a column tile size.

  Args:
    rows: Number of rows.
    cols: Number of columns.
    row_tile_size: Number of pixels in a tile row.
    col_tile_size: Number of pixels in a tile column.

  Returns:
    Tuple consisting of the number of vertical tiles and the number of horizontal tiles that the image can be divided
    into given the row tile size and the column tile size.
  """
  num_row_tiles = math.ceil(rows / row_tile_size)
  num_col_tiles = math.ceil(cols / col_tile_size)
  return num_row_tiles, num_col_tiles


def get_tile_indices(rows, cols, row_tile_size, col_tile_size):
  """
  Obtain a list of tile coordinates (starting row, ending row, starting column, ending column, row number, column number).

  Args:
    rows: Number of rows. (height in pixels)
    cols: Number of columns. (width in pixels)
    row_tile_size: Number of pixels in a tile row. (tile height in pixels)
    col_tile_size: Number of pixels in a tile column. (tile width in pixels)

  Returns:
    List of tuples representing tile coordinates consisting of starting row, ending row,
    starting column, ending column, row number, column number.

    row numbers from 1 to rows/row_tile_size (rounded up)
    column numbers from 1 to cols/col_tile_size (rounded up)
  """
  indices = list()
  num_row_tiles, num_col_tiles = get_num_tiles(rows, cols, row_tile_size, col_tile_size)
  for r in range(0, num_row_tiles):
    start_r = r * row_tile_size
    end_r = ((r + 1) * row_tile_size) if (r < num_row_tiles - 1) else rows
    for c in range(0, num_col_tiles):
      start_c = c * col_tile_size
      end_c = ((c + 1) * col_tile_size) if (c < num_col_tiles - 1) else cols
      indices.append((start_r, end_r, start_c, end_c, r + 1, c + 1))
  return indices

def tile_to_pil_tile(tile:Tile):
      """
      Convert tile information into the corresponding tile as a PIL image read from the whole-slide image file.

      Args:
        tile: Tile object.

      Return:
        Tile as a PIL image.
      """
      return ExtractTileFromWSI(tile.wsi_path, tile.get_x(), tile.get_y(), tile.get_width(), tile.get_height(), tile.level)


def tile_to_np_tile(tile):
  """
  Convert tile information into the corresponding tile as a NumPy image read from the whole-slide image file.

  Args:
    tile: Tile object.

  Return:
    Tile as a NumPy image.
  """
  pil_img = tile_to_pil_tile(tile)
  np_img = util.pil_to_np_rgb(pil_img)
  return np_img



def get_tile_image_path(tile:Tile):
  """
  Obtain tile image path based on tile information such as row, column, row pixel position, column pixel position,
  pixel width, and pixel height.

  Args:
    tile: Tile object.

  Returns:
    Path to image tile.
  """
  t = tile
  if tile.tiles_folder_path is None:
      return os.path.join(tile.tile_naming_func(tile.wsi_path) + "-" + 'tile' + "-r%d-c%d-x%d-y%d-w%d-h%d" % (
                             t.r, t.c, t.o_c_s, t.o_r_s, t.o_c_e - t.o_c_s, t.o_r_e - t.o_r_s) + "." + 'png')
  else:
      return os.path.join(tile.tiles_folder_path, 
                          tile.tile_naming_func(tile.wsi_path) + "-" + 'tile' + "-r%d-c%d-x%d-y%d-w%d-h%d" % (
                             t.r, t.c, t.o_c_s, t.o_r_s, t.o_c_e - t.o_c_s, t.o_r_e - t.o_r_s) + "." + 'png') 


def save_display_tile(tile, save, display):
  """
  Save and/or display a tile image.

  Args:
    tile: Tile object.
    save: If True, save tile image.
    display: If True, dispaly tile image.
  """
  tile_pil_img = tile_to_pil_tile(tile)

  if save:
    t = Time()
    img_path = get_tile_image_path(tile)
    dir = os.path.dirname(img_path)
    if not os.path.exists(dir):
      os.makedirs(dir)
    tile_pil_img.save(img_path)
    #print("%-20s | Time: %-14s  Name: %s" % ("Save Tile", str(t.elapsed()), img_path))

  if display:
    tile_pil_img.show()

    
def score_tiles(img_np:np.array, 
                img_np_filtered:np.array, 
                wsi_path:pathlib.Path,
                tilesFolderPath:pathlib.Path,
                tile_height:int, 
                tile_width:int,
                minimal_acceptable_tile_height:float,
                minimal_acceptable_tile_width:float,
                scale_factor:int, 
                wsi_original_width:int, 
                wsi_original_height:int, 
                wsi_scaled_width:int, 
                wsi_scaled_height:int,
                tile_score_thresh:float,
                tile_scoring_function, 
                tile_naming_func, 
                level:int, 
                best_level_for_downsample:int, 
                wsi_info:WsiInfo=None, 
                verbose=False) -> TileSummary:
    """
    Scores all tiles for a slide and returns the results in a TileSummary object.
    If regions of interests are specified, only tiles within those regions will be scored to reduce processing time.
    Tiles within those regions will be cut to fit exactly into the region. So there will be tiles that are smaller than 
    the specified height and width.

    Args:
        wsi_original_width: unscaled width with respect to the specified level
        wsi_original_height: unscaled width with respect to the specified level
        scale_factor: the scale_factor specified by the user
        best_level_for_downsample: result of openslide.OpenSlide.get_best_level_for_downsample(scale_factor) 


    Returns:
    TileSummary object which includes a list of Tile objects containing information about each tile.
    """ 
    real_scale_factor = int(math.pow(2,best_level_for_downsample-level))
    tile_height_scaled = util.adjust_level(tile_height, level, best_level_for_downsample)
    tile_width_scaled = util.adjust_level(tile_width, level, best_level_for_downsample)

    num_row_tiles, num_col_tiles = get_num_tiles(wsi_scaled_height, 
                                                 wsi_scaled_width, 
                                                 tile_height_scaled, 
                                                 tile_width_scaled)

    tile_sum = TileSummary(wsi_path=wsi_path,
                           tiles_folder_path=tilesFolderPath,
                             orig_w=wsi_original_width,
                             orig_h=wsi_original_height,
                             orig_tile_w=tile_width,
                             orig_tile_h=tile_height,
                             minimal_acceptable_tile_height = minimal_acceptable_tile_height,
                             minimal_acceptable_tile_width = minimal_acceptable_tile_width,
                             scale_factor=scale_factor,
                             scaled_w=wsi_scaled_width,
                             scaled_h=wsi_scaled_height,
                             scaled_tile_w=tile_width_scaled,
                             scaled_tile_h=tile_height_scaled,
                             tissue_percentage=filter.tissue_percent(img_np_filtered),
                             num_col_tiles=num_col_tiles,
                             num_row_tiles=num_row_tiles,
                             tile_score_thresh=tile_score_thresh,
                             level=level,
                             best_level_for_downsample=best_level_for_downsample,
                             real_scale_factor=real_scale_factor, 
                             wsi_info=wsi_info)   
    


    count = 0
    high = 0
    medium = 0
    low = 0
    none = 0
    
    rois = wsi_info.rois if wsi_info != None else None
    
    #if no rois are specified, just create one "fake" roi, that is as big as the whole image
    if(rois is None or len(rois) == 0):
        rois = [RegionOfInterest(0,0, wsi_original_height, wsi_original_width, level)]


    for roi in rois:
        # if roi level and tile extraction level differ, adjust roi dimensions to the tile level 
        # e.g. roi level is 0, tile level is 2 -> divide roi dimensions by 2^(2-0)
        if roi.level != level:
            roi.change_level_in_place(level)
                
        roi_scaled = roi.change_level_deep_copy(best_level_for_downsample)
        
        tile_indices = get_tile_indices(roi_scaled.height, roi_scaled.width, tile_height_scaled, tile_width_scaled)
        for t in tile_indices:
            count += 1  # tile_num

            #coordinates with respect to upper left point of roi as (0,0)
            r_s, r_e, c_s, c_e, r, c = t

            #coordinates with respect to upper left point of wsi as (0,0)
            r_s += roi_scaled.y_upper_left
            r_e += roi_scaled.y_upper_left
            c_s += roi_scaled.x_upper_left
            c_e += roi_scaled.x_upper_left


            np_scaled_filtered_tile = img_np_filtered[int(r_s):int(r_e), int(c_s):int(c_e)]
            t_p = filter.tissue_percent(np_scaled_filtered_tile)
            amount = tissue_quantity(t_p)
            if amount == TissueQuantity.HIGH:
                high += 1
            elif amount == TissueQuantity.MEDIUM:
                medium += 1
            elif amount == TissueQuantity.LOW:
                low += 1
            elif amount == TissueQuantity.NONE:
                none += 1
            
            o_c_s, o_r_s = slide.small_to_large_mapping((c_s, r_s), (wsi_original_width, wsi_original_height), real_scale_factor)
            #print("o_c_s: " + str(o_c_s))
            #print("o_r_s: " + str(o_r_s))
            o_c_e, o_r_e = slide.small_to_large_mapping((c_e, r_e), (wsi_original_width, wsi_original_height), real_scale_factor)
            #print("o_c_e: " + str(o_c_e))
            #print("o_r_e: " + str(o_r_e))

            # pixel adjustment in case tile dimension too large (for example, 1025 instead of 1024)
            if (o_c_e - o_c_s) > tile_width:
                o_c_e -= 1
            if (o_r_e - o_r_s) > tile_height:
                o_r_e -= 1

            score, color_factor, s_and_v_factor, quantity_factor = score_tile(np_scaled_filtered_tile, t_p, r, c, 
                                                                              tile_scoring_function)
       
            tile = Tile(tile_sum, wsi_path, tilesFolderPath, np_scaled_filtered_tile, count, r, c, r_s, r_e, c_s, c_e, o_r_s, 
                        o_r_e, o_c_s, o_c_e, t_p, color_factor, s_and_v_factor, quantity_factor, score, tile_naming_func, level, 
                        best_level_for_downsample, real_scale_factor, roi)
            if wsi_info != None:
                tile.set_wsi_info(wsi_info)              
            tile_sum.tiles.append(tile)


    tile_sum.count = count
    tile_sum.high = high
    tile_sum.medium = medium
    tile_sum.low = low
    tile_sum.none = none
      
    tiles_by_score = tile_sum.tiles_by_score()
    rank = 0
    for t in tiles_by_score:
        rank += 1
        t.rank = rank

    return tile_sum



def score_tile(np_tile, tissue_percent, row, col, scoring_function):
    """
    Score tile based on tissue percentage, color factor, saturation/value factor, and tissue quantity factor.
    
    Args:
    np_tile: Tile as NumPy array.
    tissue_percent: The percentage of the tile judged to be tissue.
    slide_num: Slide number.
    row: Tile row.
    col: Tile column.
    
    Returns tuple consisting of score, color factor, saturation/value factor, and tissue quantity factor.
    """
    color_factor = hsv_purple_pink_factor(np_tile)
    s_and_v_factor = hsv_saturation_and_value_factor(np_tile)
    amount = tissue_quantity(tissue_percent)
    quantity_factor = tissue_quantity_factor(amount)
    combined_factor = color_factor * s_and_v_factor   
    score = scoring_function(tissue_percent, combined_factor)
    
    #if combined_factor != 0.0 or tissue_percent != 0.0:
     #   print(f'before: {score}')            
                
    # scale score to between 0 and 1
    score = 1.0 - (10.0 / (10.0 + score))
    
    #if combined_factor != 0.0 or tissue_percent != 0.0:
      #  print(f'after: {score}') 
                  
    return score, color_factor, s_and_v_factor, quantity_factor

def tissue_quantity_factor(amount):
  """
  Obtain a scoring factor based on the quantity of tissue in a tile.

  Args:
    amount: Tissue amount as a TissueQuantity enum value.

  Returns:
    Scoring factor based on the tile tissue quantity.
  """
  if amount == TissueQuantity.HIGH:
    quantity_factor = 1.0
  elif amount == TissueQuantity.MEDIUM:
    quantity_factor = 0.2
  elif amount == TissueQuantity.LOW:
    quantity_factor = 0.1
  else:
    quantity_factor = 0.0
  return quantity_factor


def tissue_quantity(tissue_percentage):
  """
  Obtain TissueQuantity enum member (HIGH, MEDIUM, LOW, or NONE) for corresponding tissue percentage.

  Args:
    tissue_percentage: The tile tissue percentage.

  Returns:
    TissueQuantity enum member (HIGH, MEDIUM, LOW, or NONE).
  """
  if tissue_percentage >= TISSUE_HIGH_THRESH:
    return TissueQuantity.HIGH
  elif (tissue_percentage >= TISSUE_LOW_THRESH) and (tissue_percentage < TISSUE_HIGH_THRESH):
    return TissueQuantity.MEDIUM
  elif (tissue_percentage > 0) and (tissue_percentage < TISSUE_LOW_THRESH):
    return TissueQuantity.LOW
  else:
    return TissueQuantity.NONE



def rgb_to_hues(rgb):
  """
  Convert RGB NumPy array to 1-dimensional array of hue values (HSV H values in degrees).

  Args:
    rgb: RGB image as a NumPy array

  Returns:
    1-dimensional array of hue values in degrees
  """
  hsv = filter.filter_rgb_to_hsv(rgb, display_np_info=False)
  h = filter.filter_hsv_to_h(hsv, display_np_info=False)
  return h


def hsv_saturation_and_value_factor(rgb):
  """
  Function to reduce scores of tiles with narrow HSV saturations and values since saturation and value standard
  deviations should be relatively broad if the tile contains significant tissue.

  Example of a blurred tile that should not be ranked as a top tile:
    ../data/tiles_png/006/TUPAC-TR-006-tile-r58-c3-x2048-y58369-w1024-h1024.png

  Args:
    rgb: RGB image as a NumPy array

  Returns:
    Saturation and value factor, where 1 is no effect and less than 1 means the standard deviations of saturation and
    value are relatively small.
  """
  hsv = filter.filter_rgb_to_hsv(rgb, display_np_info=False)
  s = filter.filter_hsv_to_s(hsv)
  v = filter.filter_hsv_to_v(hsv)
  s_std = np.std(s)
  v_std = np.std(v)
  if s_std < 0.05 and v_std < 0.05:
    factor = 0.4
  elif s_std < 0.05:
    factor = 0.7
  elif v_std < 0.05:
    factor = 0.7
  else:
    factor = 1

  factor = factor ** 2
  return factor


def hsv_purple_deviation(hsv_hues):
  """
  Obtain the deviation from the HSV hue for purple.

  Args:
    hsv_hues: NumPy array of HSV hue values.

  Returns:
    The HSV purple deviation.
  """
  purple_deviation = np.sqrt(np.mean(np.abs(hsv_hues - HSV_PURPLE) ** 2))
  return purple_deviation


def hsv_pink_deviation(hsv_hues):
  """
  Obtain the deviation from the HSV hue for pink.

  Args:
    hsv_hues: NumPy array of HSV hue values.

  Returns:
    The HSV pink deviation.
  """
  pink_deviation = np.sqrt(np.mean(np.abs(hsv_hues - HSV_PINK) ** 2))
  return pink_deviation


def hsv_purple_pink_factor(rgb):
  """
  Compute scoring factor based on purple and pink HSV hue deviations and degree to which a narrowed hue color range
  average is purple versus pink.

  Args:
    rgb: Image an NumPy array.

  Returns:
    Factor that favors purple (hematoxylin stained) tissue over pink (eosin stained) tissue.
  """
  hues = rgb_to_hues(rgb)
  hues = hues[hues >= 260]  # exclude hues under 260
  hues = hues[hues <= 340]  # exclude hues over 340
  if len(hues) == 0:
    return 0  # if no hues between 260 and 340, then not purple or pink
  pu_dev = hsv_purple_deviation(hues)
  pi_dev = hsv_pink_deviation(hues)
  avg_factor = (340 - np.average(hues)) ** 2

  if pu_dev == 0:  # avoid divide by zero if tile has no tissue
    return 0

  factor = pi_dev / pu_dev * avg_factor
  return factor


def hsv_purple_vs_pink_average_factor(rgb, tissue_percentage):
  """
  Function to favor purple (hematoxylin) over pink (eosin) staining based on the distance of the HSV hue average
  from purple and pink.

  Args:
    rgb: Image as RGB NumPy array
    tissue_percentage: Amount of tissue on the tile

  Returns:
    Factor, where >1 to boost purple slide scores, <1 to reduce pink slide scores, or 1 no effect.
  """

  factor = 1
  # only applies to slides with a high quantity of tissue
  if tissue_percentage < TISSUE_HIGH_THRESH:
    return factor

  hues = rgb_to_hues(rgb)
  hues = hues[hues >= 200]  # Remove hues under 200
  if len(hues) == 0:
    return factor
  avg = np.average(hues)
  # pil_hue_histogram(hues).show()

  pu = HSV_PURPLE - avg
  pi = HSV_PINK - avg
  pupi = pu + pi
  # print("Av: %4d, Pu: %4d, Pi: %4d, PuPi: %4d" % (avg, pu, pi, pupi))
  # Av:  250, Pu:   20, Pi:   80, PuPi:  100
  # Av:  260, Pu:   10, Pi:   70, PuPi:   80
  # Av:  270, Pu:    0, Pi:   60, PuPi:   60 ** PURPLE
  # Av:  280, Pu:  -10, Pi:   50, PuPi:   40
  # Av:  290, Pu:  -20, Pi:   40, PuPi:   20
  # Av:  300, Pu:  -30, Pi:   30, PuPi:    0
  # Av:  310, Pu:  -40, Pi:   20, PuPi:  -20
  # Av:  320, Pu:  -50, Pi:   10, PuPi:  -40
  # Av:  330, Pu:  -60, Pi:    0, PuPi:  -60 ** PINK
  # Av:  340, Pu:  -70, Pi:  -10, PuPi:  -80
  # Av:  350, Pu:  -80, Pi:  -20, PuPi: -100

  if pupi > 30:
    factor *= 1.2
  if pupi < -30:
    factor *= .8
  if pupi > 0:
    factor *= 1.2
  if pupi > 50:
    factor *= 1.2
  if pupi < -60:
    factor *= .8

  return factor