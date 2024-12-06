import asyncio
import json
import random
import ssl
import time
import uuid
from loguru import logger
from aiohttp import ClientSession, ClientTimeout
from websockets_proxy import Proxy, proxy_connect
from fake_useragent import UserAgent

# Maksimum jumlah worker yang bisa dijalankan bersamaan
MAX_WORKERS = 1000
active_proxies = {}

# Skor minimal yang diperlukan untuk menggunakan proxy
MIN_SCORE = 0

# Batas kegagalan maksimal sebelum proxy dihapus
MAX_FAILURES = 5

# Daftar URL API untuk mengambil proxy
proxy_urls = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text",
    "https://www.proxy-list.download/api/v1/get?type=socks5&format=text",
    "https://www.proxy-list.download/api/v1/get?type=http&format=text",
    "https://www.proxy-list.download/api/v1/get?type=https&format=text",
    "https://www.socks-proxy.net/socks5-proxy-list",
    # Tambahkan API lainnya sesuai kebutuhan
]

async def fetch_proxies_from_api(url, session):
    """Mengambil daftar proxy dari API"""
    try:
        timeout = ClientTimeout(total=10)  # Set timeout 10 detik
        async with session.get(url, timeout=timeout) as response:
            if response.status == 200:
                content = await response.text()
                # Cek apakah respons berbentuk JSON atau teks biasa
                if content.startswith("{"):  # Jika data dalam format JSON
                    proxies = json.loads(content).get("proxies", [])
                else:  # Asumsikan teks biasa, satu proxy per baris
                    proxies = content.splitlines()
                logger.info(f"Proxy berhasil diambil dari {url}: {proxies[:5]}")  # Menampilkan 5 proxy pertama
                return proxies
            else:
                logger.error(f"Failed to fetch proxies from {url}, status code: {response.status}")
                return []
    except asyncio.TimeoutError:
        logger.error(f"Timeout while fetching proxies from {url}")
        return []
    except Exception as e:
        logger.error(f"Error fetching proxies from {url}: {e}")
        return []

async def get_proxies():
    """Ambil proxy dari beberapa URL API secara asinkron"""
    async with ClientSession() as session:
        tasks = [fetch_proxies_from_api(url, session) for url in proxy_urls]
        proxies = await asyncio.gather(*tasks)

    # Gabungkan dan hilangkan duplikat
    all_proxies = set(proxy for sublist in proxies for proxy in sublist)
    return list(all_proxies)

async def connect_to_wss(socks5_proxy, user_id, semaphore):
    """Menghubungkan ke WebSocket dengan proxy SOCKS5"""
    async with semaphore:
        user_agent = UserAgent(os=['windows', 'macos', 'linux'], browsers='chrome')
        random_user_agent = user_agent.random
        device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
        logger.info(f"Using proxy: {socks5_proxy}, device_id: {device_id}")

        if socks5_proxy not in active_proxies:
            active_proxies[socks5_proxy] = {'score': 0, 'failures': 0}

        if active_proxies[socks5_proxy]['score'] < MIN_SCORE:
            logger.info(f"Proxy {socks5_proxy} tidak memenuhi skor minimal ({MIN_SCORE}), skip.")
            return

        while True:
            try:
                await asyncio.sleep(random.randint(1, 10) / 10)
                custom_headers = {"User-Agent": random_user_agent}
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                urilist = ["wss://proxy2.wynd.network:4444/", "wss://proxy2.wynd.network:4650/"]
                uri = random.choice(urilist)
                server_hostname = "proxy2.wynd.network"
                proxy = Proxy.from_url(socks5_proxy)

                # Menghubungkan ke WebSocket
                async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, server_hostname=server_hostname, extra_headers=custom_headers) as websocket:

                    async def send_ping():
                        while True:
                            send_message = json.dumps({"id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}})
                            logger.debug(send_message)
                            await websocket.send(send_message)
                            await asyncio.sleep(5)

                    await asyncio.sleep(1)
                    asyncio.create_task(send_ping())

                    while True:
                        response = await websocket.recv()
                        message = json.loads(response)
                        logger.info(f"WebSocket message received: {message}")

                        if message.get("action") == "AUTH":
                            auth_response = {
                                "id": message["id"],
                                "origin_action": "AUTH",
                                "result": {
                                    "browser_id": device_id,
                                    "user_id": user_id,
                                    "user_agent": custom_headers['User-Agent'],
                                    "timestamp": int(time.time()),
                                    "device_type": "desktop",
                                    "version": "4.29.0",
                                }
                            }
                            logger.debug(auth_response)
                            await websocket.send(json.dumps(auth_response))

                        elif message.get("action") == "PONG":
                            pong_response = {"id": message["id"], "origin_action": "PONG"}
                            logger.debug(pong_response)
                            await websocket.send(json.dumps(pong_response))

                        else:
                            logger.warning(f"Unexpected message: {message}. Removing proxy {socks5_proxy}")
                            remove_proxy(socks5_proxy)
                            break

            except Exception as e:
                logger.error(f"Exception with proxy {socks5_proxy}: {e}")
                remove_proxy(socks5_proxy)
                break

def remove_proxy(proxy):
    """Hapus proxy dari daftar active_proxies"""
    if proxy in active_proxies:
        del active_proxies[proxy]
        logger.info(f"Proxy {proxy} removed from active proxies.")

async def save_proxies_to_file():
    """Simpan proxy yang aktif ke dalam file 'auto_proxies.txt'"""
    try:
        with open('auto_proxies.txt', 'w') as file:
            for proxy in active_proxies:
                file.write(f"{proxy}\n")
        logger.info("Active proxies have been saved to 'auto_proxies.txt'")
    except Exception as e:
        logger.error(f"Error saving proxies to file: {e}")

async def update_proxies_periodically():
    """Update proxies setiap 5 menit"""
    while True:
        logger.info("Updating proxies...")
        proxies = await get_proxies()  # Ambil proxy baru
        if proxies:
            logger.info(f"Found {len(proxies)} proxies.")
            active_proxies.clear()  # Clear proxy yang aktif
            for proxy in proxies:
                active_proxies[proxy] = {'score': 0, 'failures': 0}  # Tambahkan proxy baru
            await save_proxies_to_file()  # Simpan proxy yang baru ke file
        else:
            logger.error("No proxies found during update.")
        await asyncio.sleep(300)  # Tidur selama 5 menit

async def main():
    # Load user IDs dari 'userid_list.txt'
    try:
        with open('userid_list.txt', 'r') as file:
            user_ids = file.read().splitlines()
        if not user_ids:
            logger.error("File 'userid_list.txt' is empty. Please add user IDs.")
            return
    except FileNotFoundError:
        logger.error("File 'userid_list.txt' not found. Please create it and add user IDs.")
        return

    # Dapatkan daftar proxy dari beberapa API
    proxies = await get_proxies()
    if not proxies:
        logger.error("No proxies found. Exiting...")
        return

    # Simpan proxy pertama kali yang diambil ke file
    await save_proxies_to_file()

    # Membuat semaphore untuk membatasi jumlah koneksi bersamaan
    semaphore = asyncio.Semaphore(MAX_WORKERS)

    # Buat task untuk setiap kombinasi user_id dan proxy
    tasks = []
    for user_id in user_ids:
        for proxy in proxies:
            tasks.append(asyncio.ensure_future(connect_to_wss(proxy, user_id, semaphore)))

    # Mulai task untuk update proxy secara periodik
    asyncio.create_task(update_proxies_periodically())

    # Jalankan semua task secara bersamaan
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Kesalahan saat menjalankan task: {e}")

if __name__ == '__main__':
    # Mulai loop asyncio
    asyncio.run(main())
