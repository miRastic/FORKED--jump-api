# coding: utf-8

from cached_property import cached_property
import numpy as np
from collections import defaultdict
import weakref
from collections import OrderedDict

class ApcJournal(object):
    years = range(0, 5)

    def __init__(self, issn_l, scenario_data, scenario):
        self.scenario = weakref.proxy(scenario)
        self.settings = self.scenario.settings
        self.issn_l = issn_l
        self._scenario_data = scenario_data
        dummy = self.to_dict()  # instantiate everything


    @cached_property
    def my_data_dict(self):
        my_df = self._scenario_data["apc"]["df_by_issn_l_and_year"]
        matching_rows_df = my_df.loc[my_df.issn_l == self.issn_l]
        matching_rows_df.set_index("year", inplace=True)
        return matching_rows_df.to_dict('index')

    @cached_property
    def first_df(self):
        my_df = self._scenario_data["apc"]["df"]
        matching_rows_df = my_df.loc[my_df.issn_l == self.issn_l]
        return matching_rows_df.iloc[0].to_dict()

    @cached_property
    def title(self):
        return self.first_df["journal_name"]

    @cached_property
    def oa_status(self):
        return self.first_df["oa_status"]

    @cached_property
    def apc_2019(self):
        return self.first_df["apc"]

    @cached_property
    def num_apc_papers_historical_by_year(self):
        return [round(self.my_data_dict.get(year, defaultdict(int))["num_papers"], 4) for year in self.historical_years_by_year]

    @cached_property
    def cost_apc_historical_by_year(self):
        return [round(self.my_data_dict.get(year, defaultdict(int))["dollars"], 4) for year in self.historical_years_by_year]

    @cached_property
    def num_apc_papers_historical(self):
        return round(np.mean(self.num_apc_papers_historical_by_year), 4)

    @cached_property
    def cost_apc_historical(self):
        return round(np.mean(self.cost_apc_historical_by_year), 4)

    @cached_property
    def fractional_authorships_total_by_year(self):
        my_df = self._scenario_data["apc"]["df"]
        matching_rows_df = my_df.loc[my_df.issn_l == self.issn_l]
        my_dict_rows = matching_rows_df.to_dict('records')
        by_year = defaultdict(float)
        for my_dict in my_dict_rows:
            by_year[my_dict["year"]] += my_dict["authorship_fraction"]
        return [round(by_year.get(year, 0), 4) for year in self.historical_years_by_year]

    @cached_property
    def fractional_authorships_total(self):
        return round(np.mean(self.fractional_authorships_total_by_year), 4)

    @cached_property
    def historical_years_by_year(self):
        return range(2014, 2019)

    @cached_property
    def subscribed(self):
        return None

    @cached_property
    def is_in_package(self):
        return None

    def to_dict(self):
        response = OrderedDict()
        response["meta"] = {"issn_l": self.issn_l,
                    "title": self.title,
                    "subject": None,
                    "subscribed": self.subscribed,
                    "is_in_package": self.is_in_package
                            }
        response["cost_apc"] = int(self.cost_apc_historical)
        if self.oa_status == "gold":
            response["cost_apc_hybrid"] = 0
        else:
            response["cost_apc_hybrid"] = int(self.cost_apc_historical)
        if self.apc_2019:
            response["apc_price"] = int(self.apc_2019)
        else:
            response["apc_price"] = None
        response["fractional_authorship"] = round(self.fractional_authorships_total, 1)
        return response

    def __repr__(self):
        return u"<{} ({}) {}>".format(self.__class__.__name__, self.issn_l, self.title)


