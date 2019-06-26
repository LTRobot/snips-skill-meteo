#!/usr/bin/env python2
# coding: utf-8

import ConfigParser
from hermes_python.hermes import Hermes
from hermes_python.ontology import *
import io

import datetime
import dateutil.parser
import pytz
import json

import requests


fromtimestamp = datetime.datetime.fromtimestamp

MQTT_IP_ADDR = "localhost"
MQTT_PORT = 1883
MQTT_ADDR = "{}:{}".format(MQTT_IP_ADDR, str(MQTT_PORT))


CONFIGURATION_ENCODING_FORMAT = "utf-8"
CONFIG_INI = "config.ini"

HOSTNAME = "localhost"
HERMES_HOST = "{}:1883".format(HOSTNAME)

# WEATHER API
WEATHER_API_BASE_URL = "http://api.openweathermap.org/data/2.5"
UNITS = "metric" 






def verbalise_hour(i):
    if i == 0:
        return "minuit"
    elif i == 1:
        return "une heure"
    elif i == 12:
        return "midi"
    elif i == 21:
        return "vingt et une heures"
    else:
        return "{0} heures".format(str(i)) 


def remove_intent_prefix(full_intent_name):
    if ":" in full_intent_name:
        return full_intent_name[full_intent_name.find(":")+1:]
    else:
        return full_intent_name


class SnipsConfigParser(ConfigParser.SafeConfigParser):
    def to_dict(self):
        return {section : {option_name : option for option_name, option in self.items(section)} for section in self.sections()}


def read_configuration_file(configuration_file):
    try:
        with io.open(configuration_file, encoding=CONFIGURATION_ENCODING_FORMAT) as f:
            conf_parser = SnipsConfigParser()
            conf_parser.readfp(f)
            return conf_parser.to_dict()
    except (IOError, ConfigParser.Error) as e:
        return dict()


def get_weather_forecast(conf, slots):
    '''
    Parse the query slots, and fetch the weather forecast from Open Weather Map's API
    '''

    location = conf.get("default_city")
    time = None

    for (slot_value, slot) in slots.items():
        if slot_value in ["forecast_locality", "forecast_country", "forecast_region", "forecast_geographical_poi"]:
            location = slot[0].slot_value.value.value
        elif slot_value == "forecast_start_datetime":
            time = slot[0].slot_value.value


    forecast_url = "{0}/forecast?q={1}&APPID={2}&units={3}".format(
        WEATHER_API_BASE_URL, location, conf["secret"].get("weather_api_key"), UNITS)
    r_forecast = requests.get(forecast_url)

    return parse_open_weather_map_forecast_response(r_forecast.json(), location, time, conf)


def parse_open_weather_map_forecast_response(response, location, time, conf):
    '''
    Parse the output of Open Weather Map's forecast endpoint
    '''

    if response["message"] == "city not found":
        return None

    now = False
    contains_now = False
    more_than_a_day = False
    here = (location == conf.get("default_city"))

    if isinstance(time, dialogue.slot.TimeIntervalValue):

        from_date = dateutil.parser.parse(time.from_date)
        to_date = dateutil.parser.parse(time.to_date)

        more_than_a_day = (from_date.day != to_date.day)

        target_period_forecasts = filter(
            lambda forecast: 
                from_date <= pytz.utc.localize(fromtimestamp(forecast["dt"]))
                and pytz.utc.localize(fromtimestamp(forecast["dt"])) <= to_date 
                , response["list"]
        )

        contains_now = (from_date <= pytz.utc.localize(datetime.datetime.utcnow()))


    elif isinstance(time, dialogue.slot.InstantTimeValue):

        if time.grain >= 5:
            # Seconds, Minutes or Hours
            date = dateutil.parser.parse(time.value)

            distances = map(lambda forecast: abs(pytz.utc.localize(fromtimestamp(forecast["dt"]))-date), response["list"])
            val, idx = min((val, idx) for (idx, val) in enumerate(distances))

            target_period_forecasts = [response["list"][idx]]

        elif time.grain == 4:
            # Days
            day = dateutil.parser.parse(time.value).day
            target_period_forecasts = filter(lambda forecast: fromtimestamp(forecast["dt"]).day == day, response["list"])

        elif time.grain == 3:
            # Weeks
            date = dateutil.parser.parse(time.value)
            more_than_a_day = True

            target_period_forecasts = filter(lambda forecast: pytz.utc.localize(fromtimestamp(forecast["dt"])) >= date, response["list"])
            target_period_forecasts = filter(lambda forecast: pytz.utc.localize(fromtimestamp(forecast["dt"])) - date < datetime.timedelta(7), target_period_forecasts)

        else:
            return None


    else:
        # NOW
        now = True
        contains_now = True
        date = pytz.utc.localize(datetime.datetime.utcnow())

        distances = map(lambda forecast: abs(pytz.utc.localize(fromtimestamp(forecast["dt"]))-date), response["list"])
        val, idx = min((val, idx) for (idx, val) in enumerate(distances))
        target_period_forecasts = [response["list"][idx]]


    all_min = [x["main"]["temp_min"] for x in target_period_forecasts]
    all_max = [x["main"]["temp_max"] for x in target_period_forecasts]
    all_conditions = [x["weather"][0]["main"] for x in target_period_forecasts]
    rain_forecasts = filter(lambda forecast: forecast["weather"][0]["main"] == "Rain", target_period_forecasts)
    rain_time = fromtimestamp(rain_forecasts[0]["dt"]).hour if len(rain_forecasts) > 0 else None
    

    if len(target_period_forecasts) == 0:
        return None

    return {
        "location": location,
        "now": now,
        "containsNow": contains_now,
        "here": here,
        "moreThanADay": more_than_a_day,
        u"inLocation": " à {0}".format(location) if location else "",         
        "temperature": int(target_period_forecasts[0]["main"]["temp"]),
        "temperatureMin": int(min(all_min)),
        "temperatureMax": int(max(all_max)),
        "rainTime": rain_time,
        "mainCondition": max(set(all_conditions), key=all_conditions.count).lower()
    }


def intent_received(hermes, intent_message):

    conf = read_configuration_file(CONFIG_INI)
    
    if remove_intent_prefix(intent_message.intent.intent_name) in ['searchWeatherForecast', 'searchWeatherForecastTemperature', 'searchWeatherForecastItem', 'searchWeatherForecastCondition']:


        slots = intent_message.slots


        weather_forecast = get_weather_forecast(conf, slots)

        print(weather_forecast["mainCondition"])
        
        if weather_forecast is None:
            sentence = u"Je n'ai pas trouvé. Désolé."

        else:

            sentence = "En ce moment, "
         
            if weather_forecast["now"]:

                if weather_forecast["mainCondition"] is not None:
                    
                    if weather_forecast["mainCondition"] == "clear":
                        
                        sentence += u"Il fait beau, et"
                        
                    elif weather_forecast["mainCondition"] == "clouds":
                        
                        sentence += u"Le temps est nuageux, et"

                    elif weather_forecast["mainCondition"] == "rain":
                        
                        sentence += u"Il pleut, et"

                    elif weather_forecast["mainCondition"] == "drizzle":
                        
                        sentence += u"Il y a un peu de pluie, et"

                    elif weather_forecast["mainCondition"] == "snow":
                        
                        sentence += u"Il neige, et"

                sentence += u", il fait {0} degrés".format(weather_forecast["temperature"])
                
                if not weather_forecast["here"]:
                    
                    sentence += u"{0}".format(weather_forecast["inLocation"])

                sentence += "."



            else:

                sentence = slots.forecast_start_datetime[0].raw_value

                if weather_forecast["mainCondition"] is not None:
                    
                    if weather_forecast["mainCondition"] == "clear":
                        
                        sentence += u" il fera beau, et"
                        
                    elif weather_forecast["mainCondition"] == "clouds":
                        
                        sentence += u" le temps sera nuageux, et"

                    elif weather_forecast["mainCondition"] == "rain":
                        
                        sentence += u" il va pleuvoir, et"

                    elif weather_forecast["mainCondition"] == "drizzle":
                        
                        sentence += u" il y aura un peu de pluie, et"

                    elif weather_forecast["mainCondition"] == "snow":
                        
                        sentence += u" il va neiger, et"

                sentence += u", il va faire entre {0} et {1} degrés".format(
                    weather_forecast["temperatureMin"], 
                    weather_forecast["temperatureMax"]
                )

                if not weather_forecast["here"]:
                    sentence += u"{0}".format(weather_forecast["inLocation"])

                sentence += "."

        sentence_u = sentence.encode("utf-8") 
        print(sentence_u)

        hermes.publish_end_session(intent_message.session_id, sentence)


with Hermes(MQTT_ADDR) as h:
    h.subscribe_intents(intent_received).start()
