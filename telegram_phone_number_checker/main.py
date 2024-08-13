import asyncio
import json
import os
import re
from getpass import getpass
import click
from dotenv import load_dotenv
from telethon.sync import TelegramClient, errors, functions
from telethon.tl import types
import socks
import pandas as pd
import time
import xml.etree.ElementTree as ET
# Load environment variables from .env file
load_dotenv()

def read_proxy_settings(file_path):
    proxies = []
    with open(file_path, 'r') as f:
        for line in f:
            proxy_data = line.strip().split(',')
            if len(proxy_data) < 3:
                print(f"Skipping improperly formatted line: {line}")
                continue
            proxy = (
                socks.SOCKS5 if proxy_data[0].lower() == 'socks5' else socks.HTTP,
                proxy_data[1],
                int(proxy_data[2]),
                True,  # rdns
                proxy_data[3] if len(proxy_data) > 3 else None,
                proxy_data[4] if len(proxy_data) > 4 else None
            )
            proxies.append(proxy)
    return proxies

def read_last_checked_number(file_path):
    with open(file_path, 'r') as f:
        return f.readline().strip()

def write_last_checked_number(file_path, number):
    with open(file_path, 'w') as f:
        f.write(number)

def increment_phone_number(phone_number):
    return str(int(phone_number) + 1)

def get_human_readable_user_status(status: types.TypeUserStatus):
    if isinstance(status, types.UserStatusOnline):
        return "Currently online"
    elif isinstance(status, types.UserStatusOffline):
        return status.was_online.strftime("%Y-%m-%d %H:%M:%S %Z")
    elif isinstance(status, types.UserStatusRecently):
        return "Last seen recently"
    elif isinstance(status, types.UserStatusLastWeek):
        return "Last seen last week"
    elif isinstance(status, types.UserStatusLastMonth):
        return "Last seen last month"
    else:
        return "Unknown"

async def get_names(client: TelegramClient, phone_number: str) -> dict:
    result = {}
    print(f"Checking: {phone_number} ...", end="", flush=True)
    try:
        # Create a contact
        contact = types.InputPhoneContact(
            client_id=0, phone=phone_number, first_name="", last_name=""
        )
        # Attempt to add the contact from the address book
        contacts = await client(functions.contacts.ImportContactsRequest([contact]))

        users = contacts.to_dict().get("users", [])
        number_of_matches = len(users)

        if number_of_matches == 0:
            result.update(
                {
                    "error": "No response, the phone number is not on Telegram or has blocked contact adding."
                }
            )
        elif number_of_matches == 1:
            # Attempt to remove the contact from the address book.
            # The response from DeleteContactsRequest contains more information than from ImportContactsRequest
            updates_response: types.Updates = await client(
                functions.contacts.DeleteContactsRequest(id=[users[0].get("id")])
            )
            user = updates_response.users[0]
            # getting more information about the user
            result.update(
                {
                    "id": user.id,
                    "username": user.username,
                    "usernames": user.usernames,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "fake": user.fake,
                    "verified": user.verified,
                    "premium": user.premium,
                    "mutual_contact": user.mutual_contact,
                    "bot": user.bot,
                    "bot_chat_history": user.bot_chat_history,
                    "restricted": user.restricted,
                    "restriction_reason": user.restriction_reason,
                    "user_was_online": get_human_readable_user_status(user.status),
                    "phone": user.phone,
                }
            )
            print("results", result)
        else:
            result.update(
                {
                    "error": """This phone number matched multiple Telegram accounts, 
            which is unexpected. Please contact the developer."""
                }
            )

    except TypeError as e:
        result.update(
            {
                "error": f"TypeError: {e}. --> The error might have occurred due to the inability to delete the {phone_number} from the contact list."
            }
        )
    except errors.FloodWaitError as e:
        wait_time = e.seconds
        print(f"Rate limit hit. Waiting for {wait_time} seconds.")
        await asyncio.sleep(wait_time)
        return await get_names(client, phone_number)
    except Exception as e:
       
        result.update({"error": f"Unexpected error: {e}."})
        raise
    print("Done.")
    return result

async def validate_users(client: TelegramClient, phone_number: str, check_limit: int, file_path: str, excel_file: str) -> dict:
    result = {}
    delay = 5  # Initial delay between requests
    count = 0  # To keep track of the number of checks for this session
    while count < check_limit:
        if phone_number not in result:
            result[phone_number] = await get_names(client, phone_number)
            if "error" in result[phone_number]:
                print(f"Error for {phone_number}: {result[phone_number]['error']}")
                if "not on Telegram" in result[phone_number]["error"]:
                    phone_number = increment_phone_number(phone_number)
                    write_last_checked_number(file_path, phone_number)
                else:
                    print(f"Unexpected error for {phone_number}: {result[phone_number]['error']}")
                    break
            else:
                save_to_excel(excel_file, result[phone_number])
                phone_number = increment_phone_number(phone_number)
                write_last_checked_number(file_path, phone_number)
            await asyncio.sleep(delay)
            count += 1  # Increment the counter after each check
    return result

async def login_from_excel(proxies=None) -> dict:
    """Login using credentials from tg_id.xlsx and return all clients"""
    print("Logging in...", end="", flush=True)
    df = pd.read_excel('tg_id.xlsx')
    clients = {}
    for index, row in df.iterrows():
        api_id = row['api_id']
        api_hash = row['api_hash']
        phone_number = row['Number']
        session_name = str(phone_number)  # Use phone number as session name
        session_file_name = row.get('session_file_name', None)
        two_step_password = row.get('2step_pass', None)

        # Check if session file exists
        if pd.notna(session_file_name) and isinstance(session_file_name, str) and os.path.exists(session_file_name):
            print(f"Session file found for {phone_number}: {session_file_name}")
            continue  # Skip to next number

        # If no valid session file, proceed to login
        for proxy in proxies:
            client = TelegramClient(session_name, api_id, api_hash, proxy=proxy)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    await client.send_code_request(phone_number)
                    while True:
                        code = input(f"Enter the code for {phone_number} (sent on telegram): ")
                        try:
                            await client.sign_in(phone_number, code)
                            break  # Exit loop if sign-in is successful
                        except errors.SessionPasswordNeededError:
                            if two_step_password:
                                await client.sign_in(password=two_step_password)
                            else:
                                pw = getpass("Two-Step Verification enabled. Please enter your account password: ")
                                await client.sign_in(password=pw)
                            break  # Exit loop if sign-in is successful
                        except errors.PhoneCodeInvalidError:
                            print("Invalid code entered. Please try again.")
                session_file_name = f"{session_name}.session"
                save_session_file_name(df, index, session_file_name)
                print("Done.")
                clients[phone_number] = client
                break
            except Exception as e:
                print(f"Failed to connect with proxy {proxy}: {e}")
                await client.disconnect()
    if not clients:
        raise Exception("All proxies failed")
    return clients

def save_session_file_name(df, index, session_file_name):
    """Save the session file name to the DataFrame and Excel file."""
    df.at[index, 'session_file_name'] = session_file_name
    df.to_excel('tg_id.xlsx', index=False)
    print(f"Session file name saved for {df.at[index, 'Number']}")

def save_to_excel(file_path: str, data: dict) -> None:
    df = pd.DataFrame([data])
    
    # Exclude empty or all-NA columns from the new data
    df = df.dropna(axis=1, how='all')
    
    if os.path.exists(file_path):
        df_existing = pd.read_excel(file_path)
        
        # Exclude empty or all-NA columns from the existing data
        df_existing = df_existing.dropna(axis=1, how='all')
        
        # Concatenate only non-empty DataFrames
        if not df_existing.empty and not df.empty:
            df = pd.concat([df_existing, df], ignore_index=True)
        elif df_existing.empty:
            df = df
        else:
            df = df_existing
    
    df.to_excel(file_path, index=False)
    print(f"Results saved to {file_path}")




@click.command(
    epilog="Check out the docs at github.com/bellingcat/telegram-phone-number-checker for more information."
)
@click.option(
    "--phone-numbers-file",
    "-f",
    help="Filename containing the last checked phone number",
    type=str,
    default="last_checked_number.txt",
    show_default=True,
)
@click.option(
    "--output",
    help="Filename to store results",
    default="results.xlsx",
    show_default=True,
    type=str,
)
def main_entrypoint(phone_numbers_file: str, output: str) -> None:
    """
    Check to see if one or more phone numbers belong to a valid Telegram account.

    \b
    Prerequisites:
     1. A Telegram account with an active phone number
     2. A Telegram App api_id and App api_hash, which you can get by creating
        a Telegram App @ https://my.telegram.org/apps

    \b
    Recommendations:
    Telegram recommends entering phone numbers in international format
    +(country code)(city or carrier code)(your number)
    i.e. +491234567891

    """
    choice = input("Enter 1 to login or 2 to check numbers: ")
    proxies = read_proxy_settings('proxy.txt')
    if choice == "1":
        asyncio.run(handle_login(proxies=proxies))
    elif choice == "2":
        phone_number = read_last_checked_number(phone_numbers_file)
        asyncio.run(run_program(phone_number, output, proxies, phone_numbers_file))
    else:
        print("Invalid choice. Please enter 1 or 2.")

async def handle_login(proxies):
    clients = await login_from_excel(proxies=proxies)
    for phone_number, client in clients.items():
        await client.disconnect()

async def run_program(phone_number: str, output: str, proxies: dict, file_path: str):
    # Load clients without trying to log in again
    clients = {}
    df = pd.read_excel('tg_id.xlsx')
    
    for index, row in df.iterrows():
        session_file_name = row.get('session_file_name', None)
        check_limit = row.get('checklimit', 0)  # Get check limit from the row
        if pd.notna(session_file_name) and isinstance(session_file_name, str) and os.path.exists(session_file_name):
            clients[row['Number']] = TelegramClient(session_file_name, row['api_id'], row['api_hash'])
            print(f"Session file found for {row['Number']}: {session_file_name}")
            # Check the last checked number and validate the user
            phone_number = read_last_checked_number(file_path)
            client = clients[row['Number']]
            await client.connect()
            result = await validate_users(client, phone_number, check_limit, file_path, output)
            await client.disconnect()
        else:
            print(f"No session file found for {row['Number']}.")

if __name__ == "__main__":
    main_entrypoint()
