# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical schema for the visualization pipeline."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TimePoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: str
    y: float


class CategoryPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    value: float


class VizData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["time_series", "categorical", "table"] = "time_series"
    time_series: List[TimePoint] = Field(default_factory=list)
    categories: List[CategoryPoint] = Field(default_factory=list)
    total_cost: Optional[float] = None
    title: str = "Chart"
    x_label: str = ""
    y_label: str = ""
    chart_type: Optional[Literal["line", "bar"]] = None
    granularity: Optional[Literal["daily", "weekly", "monthly"]] = None
