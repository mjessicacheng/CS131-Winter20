import asyncio
import argparse
import logging
import time
import sys
import aiohttp
import re
import json

legal_commands = ['IAMAT', 'WHATSAT', 'AT']
google_API_key = ''
server_routes = {
    'Hill': ['Jaquez','Smith','Singleton'],
    'Jaquez': ['Hill','Singleton'],
    'Smith': ['Hill','Campbell','Singleton'],
    'Campbell': ['Smith'],
    'Singleton': ['Hill','Jaquez','Smith']
}

ports = {
    'Hill': 11620,
    'Jaquez': 11621,
    'Smith': 11622,
    'Campbell': 11623,
    'Singleton': 11624
}

def parse_coordinates(location):
    coords = []
    cell = ''
    for i in range(0,len(location)):
        if location[i] == '+':
            if i == 0:
                continue
            else:
                coords.append(cell)
                cell = ''
        elif location[i] == '-':
            if i == 0:
                cell += '-'
            else:
                coords.append(cell)
                cell = ''
        cell += location[i]
    coords.append(cell)

    latitude = float(coords[0])
    longitude = float(coords[1])

    if abs(latitude) > 90 or abs(longitude) > 180:
      return None

    return [latitude,longitude]

class Server:
    def __init__(self, name, ip='127.0.0.1'):
        self.name = name
        self.ip = ip
        self.port = ports[name]
        self.location_dict = {} #dictionary with names as key and a list of location, time, and name of server that received the WHATSAT
        self.dropped_servers = {'Hill':1,'Jaquez':1,'Smith':1,'Campbell':1,'Singleton':1} #list of dropped connections
        self.messages_received = set()

    async def parse_commands(self, reader, writer):
        '''
        on server side
        '''
        
        data = await reader.read(1000000)
        message = data.decode()
        addr = writer.get_extra_info('peername')
        check_string = ' '.join(message.split())
        #logging.info("{} received {} from {}".format(self.name, message, addr))
        command = message.split()

        if not command:
            sendback_message = '? \n'.format(message)
            writer.write(sendback_message.encode())
        elif len(command) < 4:
            sendback_message = '? {}\n'.format(message)
            writer.write(sendback_message.encode())
        elif command[0] not in legal_commands:
            sendback_message = '? {}\n'.format(message)
            writer.write(sendback_message.encode())
        elif command[0] == 'IAMAT':
            if ( (len(command) != 4) or parse_coordinates(command[2]) is None
            or not re.fullmatch('IAMAT [\s\S]+ [+-]\d*\.?\d+[+-]\d*\.?\d+ \d+\.?\d*',check_string) ):
                sendback_message = '? {}\n'.format(message)
                #logging.info('iamat re sent: {}'.format(check_string))
                writer.write(sendback_message.encode())
            else:
                await self.handle_iamat(writer,command,message)
        elif command[0] == 'WHATSAT':
            if ((len(command) != 4) or
            not re.fullmatch('WHATSAT [\s\S]+ \d*\.?\d \d+',check_string) ):
                sendback_message = '? {}\n'.format(message)
                #logging.info('whatsat re sent: {}'.format(check_string))
                writer.write(sendback_message.encode())
            else:
                if command[1] not in self.location_dict:
                    sendback_message = '? {}\n'.format(message)
                    writer.write(sendback_message.encode())
                else:
                    await self.handle_whatsat(writer,command,message)
        elif command[0] == 'AT':
            if ((len(command) != 7) or
            not re.fullmatch('AT [\s\S]+ [+-]\d+\.?\d* [\s\S]+ [+-]\d*\.?\d+[+-]\d*\.?\d+ \d+\.?\d*[\s\S]*',check_string)):
                sendback_message = '? {}\n'.format(message)
                #logging.info('at re sent: {}'.format(check_string))
                writer.write(sendback_message.encode())
            else:
                await self.handle_at(writer,command,message)
            

        await writer.drain()
        writer.close()

    async def handle_iamat(self, writer, command,message):
        name = command[1]
        location = command[2]
        t = command[3]
        try:
            timediff = time.time() - float(t)
        except:
            sendback_message = '? {}\n'.format(message)
            writer.write(sendback_message.encode())
            return
        timestring = str(timediff)
        if timediff > 0:
            timestring = '+' + timestring
        sendback_message = 'AT {} {} {} {} {}\n'.format(self.name,timestring,name,location,t)
        #update dict of locations
        if name not in self.location_dict or self.location_dict[name][1] < t:
            self.location_dict[name] = [location, t, self.name] #we are the originator
            #logging.info('updated entry {} in location_dict'.format((self.location_dict[name])))
            flood_message = sendback_message + ' ' + self.name
            asyncio.ensure_future(self.flood(flood_message,[]))
        writer.write(sendback_message.encode())
    
    async def handle_whatsat(self,writer,command,message):
        name = command[1]
        radius = float(command[2])
        information_bound = int(command[3])
        
        if radius > 50.0:
            radius = 50.0
        if information_bound > 20:
            information_bound = 20
        location = parse_coordinates(self.location_dict[name][0])
        if location is None or radius < 0 or information_bound < 0:
            sendback_message = '? {}\n'.format(message)
            writer.write(sendback_message.encode())
        else: 
            location_str = str(location[0]) + ',' + str(location[1])
            url = 'https://maps.googleapis.com/maps/api/place/nearbysearch/json?key={0}&location={1}&radius={2}'.format(google_API_key, location_str, radius)

            t = float(self.location_dict[name][1])
            timediff = time.time() - t
            timestring = str(timediff)
            if timediff > 0:
                timestring = '+' + timestring
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    response = await resp.json()
                    response['results'] = response['results'][:information_bound]
                    json_message = json.dumps(response, indent = 3) + "\n\n"
            
            sendback_message = 'AT {} {} {} {} {}\n'.format(self.location_dict[name][2],timestring,name,self.location_dict[name][0],self.location_dict[name][1])
            sendback_message += json_message
            #logging.info(sendback_message)
            writer.write(sendback_message.encode())
        
    async def handle_at(self,writer,command,message):
        server = command[1]
        t_received = command[2]
        client_name = command[3]
        location = command[4]
        t = float(command[5])
        origin = command[6]

        if server not in ports:
            sendback_message = '? {}\n'.format(message)
            writer.write(sendback_message.encode())
        elif client_name not in self.location_dict or float(self.location_dict[client_name][1]) < t: #flood
            if self.dropped_servers[server] == 1:
                self.dropped_servers[server] = 0
                logging.info("established connection with server {}".format(server))
            self.location_dict[client_name] = [location,t,origin]
            asyncio.ensure_future(self.flood(message,[server, client_name]))

    async def flood(self,message,senders):
        for server in server_routes[self.name]:
            try:
                if server not in senders and message not in self.messages_received:
                    #logging.info("attempting to send {} to {}".format(message,server))
                    reader, writer = await asyncio.open_connection(self.ip, ports[server])
                    writer.write(message.encode())
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
            except:
                #connection dropped!
                logging.info("connection dropped from server {}".format(server))
                self.dropped_servers[server] = 1
            else:
                if self.dropped_servers[server] == 1:
                    self.dropped_servers[server] = 0
                    logging.info("established connection with server {}".format(server))
        self.messages_received.add(message)

    async def run_forever(self):
        loop = asyncio.get_event_loop()
        server = await asyncio.start_server(self.parse_commands, self.ip, self.port,loop=loop)

        # Serve requests until Ctrl+C is pressed
        print(f'serving on {server.sockets[0].getsockname()}')
        async with server:
            await server.serve_forever()
        # Close the server
        server.close()

def main():
    if len(sys.argv) != 2:
        print("Invalid number of arguments")
        print("Usage: server.py [Hill | Jaquez | Smith | Campbell | Singleton]")
        exit(1)
    else:
        server_name = sys.argv[1]
        if server_name not in ports:
            print("Invalid server name")
            print("Usage: server.py [Hill | Jaquez | Smith | Campbell | Singleton]")
            exit(1) 
    
    logging.basicConfig(filename="server_{}.log".format(server_name), filemode='w', level=logging.INFO)

    server = Server(server_name)
    try:
        asyncio.run(server.run_forever())
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()    
