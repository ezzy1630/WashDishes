from wash_dishes.camera_discovery import camera_addresses


def test_discovery_accepts_only_the_enrolled_camera_and_matching_private_sender():
    response = b'''<Envelope xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><d:ProbeMatch><a:EndpointReference><a:Address>uuid:kitchen</a:Address></a:EndpointReference><d:XAddrs>http://192.168.68.51:2020/onvif/device_service</d:XAddrs></d:ProbeMatch></Envelope>'''
    assert camera_addresses([(response, '192.168.68.51')], 'uuid:kitchen') == {'192.168.68.51'}
    assert camera_addresses([(response, '192.168.68.52')], 'uuid:kitchen') == set()
    assert camera_addresses([(response, '192.168.68.51')], 'uuid:other') == set()
    assert camera_addresses([(b'not xml', '192.168.68.51')], 'uuid:kitchen') == set()


def test_camera_reconnects_only_to_its_enrolled_identity(store, media, monkeypatch):
    from wash_dishes.camera import Camera

    camera = Camera(store.root, store, media)
    config = {'host': '192.168.68.55', 'username': 'camera', 'password': 'secret', 'stream': 'stream1'}
    monkeypatch.setattr('wash_dishes.camera.neighbour_mac', lambda host: '10:5a:95:2d:1a:af')
    camera._load_discovery(config)
    monkeypatch.setattr('wash_dishes.camera.discover', lambda: [('uuid:kitchen', '192.168.68.55')])
    camera._discover_camera(config, connected=True)
    assert camera.discovery_identity == 'uuid:kitchen'

    restarted = Camera(store.root, store, media)
    restarted._load_discovery(config)
    monkeypatch.setattr('wash_dishes.camera.discover', lambda: [('uuid:other', '192.168.68.53'), ('uuid:kitchen', '192.168.68.51')])
    restarted._discover_camera(config, connected=False)
    assert restarted.resolved_host == '192.168.68.51'

    restarted._load_discovery(config)
    monkeypatch.setattr('wash_dishes.camera.neighbour_mac', lambda host: '00:00:00:00:00:01')
    restarted._discover_camera(config, connected=False)
    assert restarted.resolved_host is None

    restarted._load_discovery(config | {'username': 'another-camera'})
    restarted._discover_camera(config, connected=False)
    assert restarted.resolved_host is None
