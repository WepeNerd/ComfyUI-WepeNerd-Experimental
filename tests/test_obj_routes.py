import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

placement = importlib.import_module('wepenerd_testpkg.product_placement_node')


class ObjRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        self.input=self.root/'input'; self.models=self.root/'models'
        for folder in (self.input/'3d',self.models/'3d'):
            folder.mkdir(parents=True)
        self.valid=self.input/'3d'/'valid.obj'; self.valid.write_text('v 0 0 0\n')
        self.outside=self.root/'secret.obj'; self.outside.write_text('outside')
        self.folders=types.SimpleNamespace(get_input_directory=lambda:str(self.input),models_dir=str(self.models))
        instance=types.SimpleNamespace(routes=web.RouteTableDef())
        self.modules=mock.patch.dict(sys.modules,{'folder_paths':self.folders,'server':types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=instance))})
        self.modules.start()
        placement._register_3d_routes(); placement._register_3d_routes()
        self.assertEqual(len(instance.routes),2)
        app=web.Application(); app.add_routes(instance.routes)
        self.client=TestClient(TestServer(app)); await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close(); self.modules.stop(); self.directory.cleanup()

    async def test_valid_relative_and_absolute(self):
        for value in ('valid.obj',str(self.valid)):
            response=await self.client.get('/wepenerd/3d_product_placement/obj',params={'path':value})
            self.assertEqual(response.status,200)
            self.assertEqual(await response.read(),self.valid.read_bytes())
        model=self.models/'3d'/'model.obj'; model.write_text('v 1 0 0\n')
        self.assertEqual(placement._resolve_obj_path('model.obj'),str(model.resolve()))

    async def test_traversal_outside_missing_and_extension(self):
        for value in (str(self.outside),'../../secret.obj','missing.obj','valid.png'):
            response=await self.client.get('/wepenerd/3d_product_placement/obj',params={'path':value})
            self.assertEqual(response.status,400,value)
            with self.assertRaises(ValueError): placement._resolve_obj_path(value)

    async def test_symlink_escape(self):
        link=self.input/'3d'/'link.obj'
        try: link.symlink_to(self.outside)
        except OSError as error: self.skipTest(f'OS does not permit symlinks: {error}')
        with self.assertRaises(ValueError): placement._resolve_obj_path(str(link))

    async def test_upload_does_not_overwrite(self):
        for _ in range(2):
            form=FormData(); form.add_field('file',b'v 0 0 0\n',filename='uploaded.obj')
            response=await self.client.post('/wepenerd/3d_product_placement/upload_obj',data=form)
            self.assertEqual(response.status,200)
            payload=await response.json()
            self.assertTrue(Path(payload['path']).is_relative_to(self.input/'3d'))
            self.assertEqual(placement._resolve_obj_path(payload['path']),payload['path'])
        self.assertTrue((self.input/'3d'/'uploaded (1).obj').is_file())


class OptionalDependencies(unittest.TestCase):
    def test_invalid_viewport_capture_remains_ignorable(self):
        from PIL import Image
        for value in ('data:image/png;base64,%%%', 'data:image/png;base64,bm90IGEgcG5n'):
            self.assertIsNone(placement._data_url_to_pil(value, Image))

    def test_missing_renderer_fails_only_when_requested(self):
        with mock.patch.dict(sys.modules,{'trimesh':None,'pyrender':None}):
            with self.assertRaisesRegex(ImportError,'requirements-3d'):
                placement._import_3d_dependencies()
