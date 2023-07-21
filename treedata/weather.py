import os
import argparse

from sqlalchemy import text
import psycopg2
import psycopg2.extras

from radolan.buffer_city_shape import create_buffered_city_shape
from radolan.download_weather_data import download_weather_data
from radolan.extract_weather_data import extract_weather_data
from radolan.polygonize_weather_data import polygonize_weather_data
from radolan.join_radolan_data import join_radolan_data, get_radolan_geometry
from radolan.upload_radolan import upload_radolan_data, purge_data_older_than_time_limit_days, purge_duplicates
from radolan.create_radolan_schemas import create_radolan_schema
from radolan.update_tree_radolan_days import get_weather_data_grid_cells, get_sorted_cleaned_grid_cells, \
    update_tree_radolan_days, update_statistics_db
from utils.interact_with_database import get_db_engine
from utils.get_data_from_wfs import store_as_geojson, read_geojson

ROOT_DIR = os.path.abspath(os.curdir)
TIME_LIMIT_DAYS = 30


def configure_weather_args(parser=argparse.ArgumentParser(description='Process weather data')):
    parser.add_argument('--start-days-offset', dest='start_days_offset', action='store',
                        help='number of days from today in past to start downloading radolan data', default=2)
    parser.add_argument('--end-days-offset', dest='end_days_offset', action='store',
                        help='number of days from today in past to stop downloading radolan data', default=1)
    parser.add_argument('--city-shape-geojson-file-name', dest='city_shape_file_name', action='store',
                        help='Provide GeoJSON file name of city shape to use', default='city_shape')
    parser.add_argument('--city-shape-buffer-file-name', dest='city_shape_buffer_file_name', action='store',
                        help='file name to store buffered city shape under', default='city_shape-buffered')
    parser.add_argument('--city-shape-buffer', dest='city_shape_buffer', action='store',
                        help='buffer to apply for buffering city shape', default=2000)
    parser.add_argument('--city-shape-simplify', dest='city_shape_simplify', action='store',
                        help='simplify factor to apply for simplifying city shape', default=1000)
    parser.add_argument('--skip-download-weather-data', dest='skip_download_weather_data', action='store_true',
                        help='skip step of downloading radolan data', default=False)
    parser.add_argument('--skip-unzip-weather-data', dest='skip_unzip_weather_data', action='store_true',
                        help='skip step of unzipping radolan data', default=False)
    parser.add_argument('--skip-buffer-city-shape', dest='skip_buffer_city_shape', action='store_true',
                        help='skip step of creating buffer shape of city shape', default=False)
    parser.add_argument('--skip-polygonize-weather-data', dest='skip_polygonize_weather_data', action='store_true',
                        help='skip step of polygnize radolan data as shape files', default=False)
    parser.add_argument('--skip-join-radolan-data', dest='skip_join_radolan_data', action='store_true',
                        help='skip step of joining radolan shp files to geojson', default=False)
    parser.add_argument('--skip-upload-radolan-data', dest='skip_upload_radolan_data', action='store_true',
                        help='skip step of upload radolan geojson to DB', default=False)
    parser.add_argument('--skip-update-tree-radolan-days', dest='skip_update_tree_radolan_days', action='store_true',
                        help='skip step of updating trees with radolan days', default=False)
    parser.set_defaults(which='weather', func=handle_weather)


def handle_weather(args):
    if not args.skip_buffer_city_shape:
        create_buffered_city_shape(
            input_file_name=args.city_shape_file_name,
            output_file_name=args.city_shape_buffer_file_name,
            buffer_radius=args.city_shape_buffer,
            simplify_tolerance=args.city_shape_simplify
        )
    if not args.skip_download_weather_data:
        download_weather_data(
            start_days_offset=int(args.start_days_offset),
            end_days_offset=int(args.end_days_offset),
        )
    if not args.skip_unzip_weather_data:
        extract_weather_data()
    if not args.skip_polygonize_weather_data:
        filelist, last_received = polygonize_weather_data(args.city_shape_buffer_file_name)
        db_engine = get_db_engine()
        update_statistics_db(filelist, db_engine, TIME_LIMIT_DAYS, last_received)
    joined_path = f"{ROOT_DIR}/resources/radolan/radolan-joined"
    if not args.skip_join_radolan_data:
        radolan_data = join_radolan_data()
        store_as_geojson(radolan_data, joined_path)
    else:
        radolan_data = read_geojson(f"{joined_path}.geojson")
    if not args.skip_upload_radolan_data:
        db_engine = get_db_engine()
        create_radolan_schema(db_engine)
        radolan_geometry = get_radolan_geometry()
        # TODO store in DB
        #with db_engine.connect() as conn:
        #    conn.begin()
        #    for entry in radolan_geometry:
        #        conn.execute(
        #            text('INSERT INTO public.radolan_geometry("geometry", "centroid") VALUES (%s, %s)'),
        #            [entry['geometry'], entry['centroid']]
        #        )
        #    conn.commit()
        #radolan_geometry.to_postgis('radolan_geometry', db_engine, if_exists='replace', index=True, index_label='id')
        upload_radolan_data(db_engine, radolan_data)
        purge_data_older_than_time_limit_days(db_engine, TIME_LIMIT_DAYS)
        purge_duplicates(db_engine)
    if not args.skip_update_tree_radolan_days:
        db_engine = get_db_engine()
        grid = get_weather_data_grid_cells(engine=db_engine, time_limit_days=TIME_LIMIT_DAYS)
        values = get_sorted_cleaned_grid_cells(grid, TIME_LIMIT_DAYS)
        update_tree_radolan_days(db_engine, values)
