import {render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import ProjectPractice from '@/components/learning/ProjectPractice';
const {list}=vi.hoisted(()=>({list:vi.fn()}));
vi.mock('react-i18next',()=>({useTranslation:()=>({t:(s:string)=>s})}));
vi.mock('@/lib/notebook-api',()=>({listNotebookEntries:list}));
it('requests only explicitly associated project questions',async()=>{
 list.mockResolvedValue({items:[{id:8,question:'Project task',is_correct:true}],total:1});
 render(<ProjectPractice pathId="p-one"/>);
 expect(await screen.findByText('Project task')).toBeVisible();
 expect(list).toHaveBeenCalledWith({mastery_path_id:'p-one',limit:30,offset:0});
 expect(screen.getByRole('link',{name:/Project task/})).toHaveAttribute('href',expect.stringContaining('question=8'));
});
