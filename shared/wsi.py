from __future__ import annotations #https://stackoverflow.com/questions/33837918/type-hints-solve-circular-dependency

import numpy
import numpy as np
from shared.case import Case
import typing
from typing import Dict


class WholeSlideImage:
    case:Case = None
    regions_of_interest:List[RegionOfInterest] = None
    slide_id:str = None
    path:pathlib.Path = None
    predictions_raw:Dict[str, float] = None # key: class name; value: tiles with that class / all tiles
    predictions_thresh:Dict[str, bool] = None # key: class name; value: bool
      
    def __init__(self, slide_id:str, case:Case, path=None):
        self.case = case
        self.slide_id = slide_id
        self.path = path
        self.regions_of_interest = []
        
    def get_tiles(self)-> List[shared.tile.Tile]:
        tls = []
        for roi in self.regions_of_interest:
            for tile in roi.tiles:
                tls.append(tile)
        return tls
    
    def get_labels(self)-> List[Union[str, int]]:
        """
        iterates over all its associated tiles and returns a list of all found classes
        """
        labels = []
        for t in self.get_tiles():
            for l in t.labels:
                labels.append(l)
        return list(set(labels))
    
    def get_predictions_one_hot_encoded(self)->numpy.ndarray:
        """
            Returns:
                numpy array with one hot encoded labels
        """
        return np.array(list(self.predictions_thresh.values())).astype(np.int0)
    
    def get_labels_one_hot_encoded(self, vocab = None)->numpy.ndarray:
        """
            Arguments:
                vocab: A list of the used classes. It is very important that the order is the same as in 
                        learner.dls.vocab. The default value only works, if predict_on_tiles() has already been called.
            Returns:
                numpy array with one hot encoded labels
        """
        if(vocab == None and self.predictions_raw == None):
            raise ValueError('Either specify a vocab (e.g. learner.dls.vocab) or call Predictor.predict_on_tiles())')
        elif(vocab == None):
            vocab = self.predictions_raw.keys()
        
        labels_one_hot_encoded = np.zeros(len(vocab))
        labels_list = self.get_labels()
        for n, Class in enumerate(vocab):
            if(Class in labels_list):
                labels_one_hot_encoded[n] = 1
        return labels_one_hot_encoded.astype(np.int0)