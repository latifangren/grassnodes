#!/bin/bash
cd grassnodes/'node mode'
screen -S noproxy python3 localgrassnode_noproxy.py
screen -S autonode python3 localgrassnode_autoproxy.py
screen -S localproxi python3 localgrassnode.py
