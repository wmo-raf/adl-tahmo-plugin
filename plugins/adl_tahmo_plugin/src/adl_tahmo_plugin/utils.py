def get_stations(network_conn):
    client = network_conn.get_api_client()
    
    stations_dict = client.get_stations()
    
    stations_list = []
    
    for key, station in stations_dict.items():
        station_code = station.get("code")
        station_name = station.get("location", {}).get("name", "")
        station_label = f"{station_name} ({station_code})"
        stations_list.append({"label": station_label, "value": station_code})
    
    return stations_list
