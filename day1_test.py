grade=60

print('执行判断1')
if grade >=60 and grade<70:
    print('D')

fruits=['apple','banana','cherry']
for X in fruits:
    print(X)

i=1
while i<6:
    print(i)   
    i+=1

def relax():
    print('起床')
    print('刷牙')
    print('洗脸')
relax()

def price(X):
    sumall=X*160
    return sumall
price(30)
allprice=price(30)
print(allprice)