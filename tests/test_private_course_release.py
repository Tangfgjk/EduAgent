from sqlalchemy import select
from app.infrastructure.db.models import GenerationPolicy,ModelOperation,CourseBuild
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.worker import Worker
from tests.test_s02 import database,env
from tests.test_topic_generation import prepare,confirm


def test_private_course_is_opened_only_after_math_and_two_machine_reviews(env):
    client,wid,root,row=prepare(env)
    queued=confirm(env,client,root,row)
    policy=env.admin.get('/api/v1/admin/generation-policy').json()
    assert env.admin.post('/api/v1/admin/generation-policy',json={'expected_revision':policy['revision'],
        'courses':True,'interaction':True,'search':False,'private_auto_release':True,'acknowledge_development_only':True}).status_code==200
    assert ModelTaskRunner(env.db).step(operation_id=queued['operation']['operation_id'])
    for index in (1,2):
        assert Worker(env.db,enable_models=False).step()
        with env.db.transaction() as session:
            build=session.get(CourseBuild,row['build_id'])
            assert build.status=='semantic_review'
            op=session.scalar(select(ModelOperation).where(ModelOperation.run_id==build.run_id))
            assert op.request['purpose']=='course_semantic_review' and op.request['review_index']==index
            oid=op.id
        assert ModelTaskRunner(env.db).step(operation_id=oid)
    response=client.get(root+'/'+row['build_id'])
    assert response.status_code==200,response.text
    build=response.json()
    assert build['status']=='ready',build
    assert len(build['validation']['machine_reviews'])==2
    assert build['validation']['release_decision']['kind']=='system_auto'
    assert not build['validation']['release_decision']['professional_review']
