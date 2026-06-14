#!/usr/bin/env python3
"""
NOAA Meteorological Observations Collector

Collects met data from NOAA CO-OPS station 9414290 (San Francisco).
Provides wind speed, direction, barometric pressure, and air temperature.

Wind direction is critical for the model:
- West winds → orographic lifting → more rain than models predict
- South winds → rain shadow → less rain than models predict

See: nws_rain.py NOAACoopsCollector for the implementation.
This module provides additional met-specific analysis.
"""

# Implementation is in nws_rain.py NOAACoopsCollector
# This file reserved for future met-specific analysis functions

from src.collectors.nws_rain import NOAACoopsCollector

__all__ = ["NOAACoopsCollector"]
