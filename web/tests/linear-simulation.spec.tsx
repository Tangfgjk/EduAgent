import {render,screen,fireEvent} from "@testing-library/react";
import {test,expect,vi} from "vitest";
import Linear from "@/components/learning/LinearSimulation";
test('moving the point changes the equation calculation and persists the operation',()=>{
 const submit=vi.fn();render(<Linear spec={{a:2,b:3,c:8,title:'坐标实验'}} state={{prediction:'预测点1,2符合'}} busy={false} submit={submit}/>);
 fireEvent.change(screen.getByLabelText('坐标x'),{target:{value:'1'}});fireEvent.change(screen.getByLabelText('坐标y'),{target:{value:'2'}});
 expect(screen.getByText(/左边=8，右边=8/)).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'记录这次操作'}));expect(submit).toHaveBeenCalledWith('operate','',[1,2]);
});
