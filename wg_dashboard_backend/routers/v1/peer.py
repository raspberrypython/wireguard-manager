import ipaddress

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import const
import models
import schemas
import middleware
import db.wireguard
import script.wireguard

router = APIRouter()


@router.post("/add", response_model=schemas.WGPeer)
def add_peer(
        peer_add: schemas.WGPeerAdd,
        sess: Session = Depends(middleware.get_db)
):
    server = schemas.WGServer(interface=peer_add.server_interface).from_db(sess)
    peer = schemas.WGPeer(server_id=server.id)

    address_space = set(ipaddress.ip_network(server.address, strict=False).hosts())
    occupied_space = set()
    for p in server.peers:
        try:
            occupied_space.add(ipaddress.ip_address(p.address.split("/")[0]))
        except ValueError as e:
            pass  # Ignore invalid addresses. These are out of address_space

    address_space -= occupied_space

    # Select first available address
    peer.address = str(list(sorted(address_space)).pop(0)) + "/32"

    # Private public key generation
    keys = script.wireguard.generate_keys()
    peer.private_key = keys["private_key"]
    peer.public_key = keys["public_key"]

    # Set 0.0.0.0/0, ::/0 as default allowed ips
    peer.allowed_ips = ', '.join(const.PEER_DEFAULT_ALLOWED_IPS)

    # Set unnamed
    peer.name = "Unnamed"

    peer.dns = server.endpoint

    peer.configuration = script.wireguard.generate_config(dict(
        peer=peer,
        server=server
    ))

    peer.sync(sess)

    # If server is running. Add peer
    if script.wireguard.is_running(server):
        script.wireguard.add_peer(server, peer)

    return peer


@router.post("/delete", response_model=schemas.WGPeer)
def delete_peer(
        peer: schemas.WGPeer,
        sess: Session = Depends(middleware.get_db)
):
    peer.from_db(sess)  # Sync full object

    if not db.wireguard.peer_remove(sess, peer):
        raise HTTPException(400, detail="Were not able to delete peer %s (%s)" % (peer.name, peer.public_key))

    server = schemas.WGServer(interface=peer.server_id)
    if script.wireguard.is_running(server):
        script.wireguard.remove_peer(server, peer)

    return peer


@router.post("/edit", response_model=schemas.WGPeer)
def edit_peer(
        peer: schemas.WGPeer,
        sess: Session = Depends(middleware.get_db)
):
    server = schemas.WGServer(interface="")\
            .from_orm(sess.query(models.WGServer).filter_by(id=peer.server_id).one())

    peer.configuration = script.wireguard.generate_config(dict(
        peer=peer,
        server=server
    ))
    peer.sync(sess)

    return peer
