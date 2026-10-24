"""QR code (ISO/IEC 18004) for MFA enrollment links: byte mode, error correction level M,
versions 1-10 (up to 213 bytes). Returns an SVG, so no script library is needed in the page."""

# Per version: (EC codewords per block, [(block count, data codewords per block), ...]) for level M.
BLOCKS={1:(10,[(1,16)]),2:(16,[(1,28)]),3:(26,[(1,44)]),4:(18,[(2,32)]),5:(24,[(2,43)]),6:(16,[(4,27)]),
        7:(18,[(4,31)]),8:(22,[(2,38),(2,39)]),9:(22,[(3,36),(2,37)]),10:(26,[(4,43),(1,44)])}
ALIGNMENT={1:[],2:[6,18],3:[6,22],4:[6,26],5:[6,30],6:[6,34],7:[6,22,38],8:[6,24,42],9:[6,26,46],10:[6,28,50]}
REMAINDER_BITS={1:0,2:7,3:7,4:7,5:7,6:7,7:0,8:0,9:0,10:0}

EXP=[0]*512;LOG=[0]*256
_x=1
for _i in range(255):
    EXP[_i]=_x;LOG[_x]=_i;_x<<=1
    if _x&0x100:_x^=0x11D
for _i in range(255,512):EXP[_i]=EXP[_i-255]

def gf_mul(a,b):
    return 0 if a==0 or b==0 else EXP[LOG[a]+LOG[b]]

def rs_remainder(data,degree):
    generator=[1]
    for i in range(degree):
        generator=[a^gf_mul(b,EXP[i]) for a,b in zip(generator+[0],[0]+generator)]
    remainder=[0]*degree
    for byte in data:
        factor=byte^remainder[0]
        remainder=remainder[1:]+[0]
        for i in range(degree):remainder[i]^=gf_mul(generator[i+1],factor)
    return remainder

def codewords(data,version):
    ec,groups=BLOCKS[version];capacity=sum(n*k for n,k in groups)
    count_bits=8 if version<10 else 16
    bits=[0,1,0,0]+[(len(data)>>i)&1 for i in reversed(range(count_bits))]+[(b>>i)&1 for b in data for i in reversed(range(8))]
    bits+=[0]*min(4,capacity*8-len(bits))
    bits+=[0]*(-len(bits)%8)
    out=[int(''.join(map(str,bits[i:i+8])),2) for i in range(0,len(bits),8)]
    for i in range(capacity-len(out)):out.append((0xEC,0x11)[i%2])
    blocks=[];position=0
    for n,k in groups:
        for _ in range(n):blocks.append(out[position:position+k]);position+=k
    ecc=[rs_remainder(b,ec) for b in blocks]
    result=[b[i] for i in range(max(len(b) for b in blocks)) for b in blocks if i<len(b)]
    return result+[e[i] for i in range(ec) for e in ecc]

def bch(value,generator,bits):
    shifted=value<<(generator.bit_length()-1)
    for i in reversed(range(bits)):
        if shifted>>(i+generator.bit_length()-1)&1:shifted^=generator<<i
    return (value<<(generator.bit_length()-1))|shifted

class Matrix:
    def __init__(self,version):
        self.version=version;self.size=17+4*version
        self.dark=[[False]*self.size for _ in range(self.size)];self.function=[[False]*self.size for _ in range(self.size)]
    def set(self,x,y,dark,function=True):
        self.dark[y][x]=dark
        if function:self.function[y][x]=True
    def patterns(self):
        n=self.size
        for i in range(n):
            self.set(6,i,i%2==0);self.set(i,6,i%2==0)
        for cx,cy in ((3,3),(n-4,3),(3,n-4)):
            for dy in range(-4,5):
                for dx in range(-4,5):
                    x,y=cx+dx,cy+dy
                    if 0<=x<n and 0<=y<n:self.set(x,y,max(abs(dx),abs(dy)) not in (2,4))
        positions=ALIGNMENT[self.version]
        for i,cx in enumerate(positions):
            for j,cy in enumerate(positions):
                if (i==0 and j==0) or (i==0 and j==len(positions)-1) or (i==len(positions)-1 and j==0):continue
                for dy in range(-2,3):
                    for dx in range(-2,3):self.set(cx+dx,cy+dy,max(abs(dx),abs(dy))!=1)
        self.format_bits(0)
        if self.version>=7:
            bits=bch(self.version,0x1F25,6)
            for i in range(18):
                a,b=n-11+i%3,i//3;dark=bits>>i&1==1
                self.set(a,b,dark);self.set(b,a,dark)
    def format_bits(self,mask):
        # Level M is 00 in the format information.
        bits=bch(mask,0x537,5)^0x5412;n=self.size
        for i in range(6):self.set(8,i,bits>>i&1==1)
        self.set(8,7,bits>>6&1==1);self.set(8,8,bits>>7&1==1);self.set(7,8,bits>>8&1==1)
        for i in range(9,15):self.set(14-i,8,bits>>i&1==1)
        for i in range(8):self.set(n-1-i,8,bits>>i&1==1)
        for i in range(8,15):self.set(8,n-15+i,bits>>i&1==1)
        self.set(8,n-8,True)
    def place(self,data):
        n=self.size;bits=[(b>>(7-i))&1 for b in data for i in range(8)]+[0]*REMAINDER_BITS[self.version];i=0
        right=n-1
        while right>=1:
            if right==6:right=5
            for vertical in range(n):
                for j in range(2):
                    x=right-j;upward=((right+1)&2)==0;y=n-1-vertical if upward else vertical
                    if not self.function[y][x] and i<len(bits):self.dark[y][x]=bits[i]==1;i+=1
            right-=2
    def apply_mask(self,mask):
        rules=[lambda x,y:(x+y)%2==0,lambda x,y:y%2==0,lambda x,y:x%3==0,lambda x,y:(x+y)%3==0,lambda x,y:(x//3+y//2)%2==0,
               lambda x,y:x*y%2+x*y%3==0,lambda x,y:(x*y%2+x*y%3)%2==0,lambda x,y:((x+y)%2+x*y%3)%2==0]
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and rules[mask](x,y):self.dark[y][x]=not self.dark[y][x]
    def penalty(self):
        n=self.size;grid=self.dark;score=0
        lines=[row for row in grid]+[[grid[y][x] for y in range(n)] for x in range(n)]
        for line in lines:
            run=1
            for i in range(1,n+1):
                if i<n and line[i]==line[i-1]:run+=1
                else:
                    if run>=5:score+=run-2
                    run=1
            padded=[False]*4+line+[False]*4
            for i in range(len(padded)-10):
                window=padded[i:i+11]
                if window==[True,False,True,True,True,False,True,False,False,False,False] or window==[False,False,False,False,True,False,True,True,True,False,True]:score+=40
        for y in range(n-1):
            for x in range(n-1):
                if grid[y][x]==grid[y][x+1]==grid[y+1][x]==grid[y+1][x+1]:score+=3
        dark=sum(map(sum,grid));total=n*n
        score+=((abs(dark*20-total*10)+total-1)//total-1)*10
        return score

def encode(text,mask=None):
    data=text.encode()
    version=next((v for v in BLOCKS if len(data)<=sum(n*k for n,k in BLOCKS[v][1])-(2 if v<10 else 3)),None)
    if version is None:raise ValueError('text too long for a QR code up to version 10')
    words=codewords(data,version)
    best=None
    for m in ([mask] if mask is not None else range(8)):
        matrix=Matrix(version);matrix.patterns();matrix.place(words);matrix.apply_mask(m);matrix.format_bits(m)
        score=matrix.penalty()
        if best is None or score<best[0]:best=(score,matrix)
    return best[1].dark

def svg(text,scale=6,quiet=4):
    grid=encode(text);n=len(grid);size=(n+2*quiet)*scale
    path=''.join(f'M{(x+quiet)*scale},{(y+quiet)*scale}h{scale}v{scale}h-{scale}z' for y in range(n) for x in range(n) if grid[y][x])
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="QRコード"><rect width="100%" height="100%" fill="#fff"/><path d="{path}" fill="#000"/></svg>'
