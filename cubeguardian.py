import asyncio
import aiohttp
import os
import yaml
import argparse
from loguru import logger

class Style:
    BOLD = '\033[1m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    ENDC = '\033[0m'

def setup_logger():
    logger.remove()
    logger.add(lambda msg: print(msg), format="{message}")

setup_logger()

def read_config(config_file='config.yaml'):
    try:
        with open(config_file, 'r') as file:
            config = yaml.safe_load(file)
        if 'api_url' not in config or 'api_token' not in config:
            raise ValueError("Missing required configuration items in config.yaml")
        return config
    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file '{config_file}' not found.")
    except yaml.YAMLError as exc:
        raise ValueError(f"Error parsing YAML file: {exc}")

async def http_request(session, method, url, **kwargs):
    async with session.request(method, url, **kwargs) as response:
        if response.status != 200:
            error_json = await response.json()
            error_detail = error_json.get('error', 'Unknown error')
            raise aiohttp.ClientError(f"{error_detail}")
        return await response.json()

async def get_cube_metadata(session, api_url, api_token):
    headers = {'Authorization': f'Bearer {api_token}'}
    return await http_request(session, 'GET', f"{api_url}/meta", headers=headers)

async def test_cube(session, api_url, cube_details, api_token):
    headers = {'Authorization': f'Bearer {api_token}'}
    query = {"measures": cube_details.get("measures", []), "dimensions": cube_details.get("dimensions", []), "limit": 1}
    await http_request(session, 'POST', f"{api_url}/load", headers=headers, json={'query': query})

async def test_dimension(session, api_url, cube_name, dimension, measure, api_token):
    headers = {'Authorization': f'Bearer {api_token}'}
    query = {"measures": [measure], "dimensions": [dimension], "limit": 1}
    try:
        await http_request(session, 'POST', f"{api_url}/load", headers=headers, json={'query': query})
    except aiohttp.ClientError as e:
        return f"Dimension {dimension} failed: {str(e)}"

async def test_cube_with_semaphore(semaphore, session, api_url, cube_details, api_token, all_cubes_status, detailed_error_messages, fail_fast):
    async with semaphore:
        cube_name = cube_details['name']
        try:
            await test_cube(session, api_url, cube_details, api_token)
            all_cubes_status[cube_name] = 'passed'
        except aiohttp.ClientError as error:
            all_cubes_status[cube_name] = 'failed'
            for dimension in cube_details['dimensions']:
                error_message = await test_dimension(session, api_url, cube_name, dimension, cube_details['measures'][0] if cube_details['measures'] else "", api_token)
                if error_message:
                    detailed_error_messages.append(f"Cube {cube_name}: {error_message}")
                    if fail_fast:
                        break

async def run_tests(api_url, api_token, concurrency_limit, specific_cubes=None, fail_fast=False):
    start_time = asyncio.get_event_loop().time()
    async with aiohttp.ClientSession() as session:
        metadata = await get_cube_metadata(session, api_url, api_token)
        semaphore = asyncio.Semaphore(concurrency_limit)
        all_cubes_status = {}
        detailed_error_messages = []

        cubes_to_test = metadata['cubes'] if specific_cubes is None else [cube for cube in metadata['cubes'] if cube['name'] in specific_cubes]

        tasks = [asyncio.create_task(test_cube_with_semaphore(semaphore, session, api_url, 
                 {'name': cube['name'], 'measures': [m['name'] for m in cube.get('measures', [])], 
                 'dimensions': [d['name'] for d in cube.get('dimensions', [])]},
                 api_token, all_cubes_status, detailed_error_messages, fail_fast)) for cube in cubes_to_test]

        await asyncio.gather(*tasks)

        log_summary(all_cubes_status, detailed_error_messages, start_time)

def log_summary(all_cubes_status, detailed_error_messages, start_time):
    logger.info("=" * 100)
    logger.info("Cube Status Summary")
    logger.info("=" * 100)
    for cube_name, status in all_cubes_status.items():
        if status == 'passed':
            logger.info(f"{Style.GREEN}{Style.BOLD}✅ Cube {cube_name} passed{Style.ENDC}")
        else:
            logger.info(f"{Style.RED}⛔ Cube {cube_name} failed{Style.ENDC}")

    if detailed_error_messages:
        logger.info("=" * 100)
        logger.info("Detailed Errors")
        logger.info("=" * 100)
        for message in detailed_error_messages:
            logger.error(message)

    duration = asyncio.get_event_loop().time() - start_time
    logger.info("=" * 100)
    logger.info(f"Completed cube validation in {duration:.2f} seconds.")

def get_env_variables():
    API_URL = os.getenv("API_URL")
    API_TOKEN = os.getenv("API_TOKEN")
    if not API_URL or not API_TOKEN:
        raise EnvironmentError("API_URL and API_TOKEN must be set as environment variables.")
    return API_URL, API_TOKEN

def parse_arguments():
    parser = argparse.ArgumentParser(description="Run cube tests with optional fail-fast, cube selection, and concurrency control features.")
    parser.add_argument("--fail-fast", action="store_true", help="Enable fail-fast mode. Stops testing further dimensions on the first failure.")
    parser.add_argument("--cubes", nargs="+", help="Specify a list of cube names to test. If not set, all cubes will be tested.")
    parser.add_argument("--concurrency", type=int, default=10, help="Set the concurrency limit for testing. Default is 10.")
    return parser.parse_args()

def main():
    args = parse_arguments()
    config = read_config()
    API_URL = config['api_url']
    API_TOKEN = config['api_token']
    asyncio.run(run_tests(API_URL, API_TOKEN, args.concurrency, specific_cubes=args.cubes, fail_fast=args.fail_fast))

if __name__ == "__main__":
    main()
