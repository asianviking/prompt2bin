	.file	"tmpcmm5uu8q.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	frame_scratch_create
	.type	frame_scratch_create, @function
frame_scratch_create:
	endbr64
	sub	rsp, 8
	mov	esi, 65568
	mov	edi, 8
	call	aligned_alloc@PLT
	test	rax, rax
	je	.L1
	lea	rdx, 39[rax]
	movdqa	xmm0, XMMWORD PTR .LC0[rip]
	mov	QWORD PTR 24[rax], 0
	and	rdx, -8
	mov	QWORD PTR [rax], rdx
	movups	XMMWORD PTR 8[rax], xmm0
.L1:
	add	rsp, 8
	ret
	.size	frame_scratch_create, .-frame_scratch_create
	.p2align 4
	.globl	frame_scratch_alloc
	.type	frame_scratch_alloc, @function
frame_scratch_alloc:
	endbr64
	lea	rax, -1[rsi]
	cmp	rax, 65519
	ja	.L11
	mov	rax, QWORD PTR 8[rdi]
	add	rax, 7
	and	rax, -8
	add	rsi, rax
	cmp	QWORD PTR 16[rdi], rsi
	jb	.L11
	mov	QWORD PTR 8[rdi], rsi
	add	rax, QWORD PTR [rdi]
	ret
	.p2align 4,,10
	.p2align 3
.L11:
	xor	eax, eax
	ret
	.size	frame_scratch_alloc, .-frame_scratch_alloc
	.p2align 4
	.globl	frame_scratch_reset
	.type	frame_scratch_reset, @function
frame_scratch_reset:
	endbr64
	add	QWORD PTR 24[rdi], 1
	mov	QWORD PTR 8[rdi], 0
	ret
	.size	frame_scratch_reset, .-frame_scratch_reset
	.p2align 4
	.globl	frame_scratch_destroy
	.type	frame_scratch_destroy, @function
frame_scratch_destroy:
	endbr64
	test	rdi, rdi
	je	.L13
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L13:
	ret
	.size	frame_scratch_destroy, .-frame_scratch_destroy
	.p2align 4
	.globl	_force_create
	.type	_force_create, @function
_force_create:
	endbr64
	sub	rsp, 8
	mov	esi, 65568
	mov	edi, 8
	call	aligned_alloc@PLT
	test	rax, rax
	je	.L15
	lea	rdx, 39[rax]
	movdqa	xmm0, XMMWORD PTR .LC0[rip]
	mov	QWORD PTR 24[rax], 0
	and	rdx, -8
	mov	QWORD PTR [rax], rdx
	movups	XMMWORD PTR 8[rax], xmm0
.L15:
	add	rsp, 8
	ret
	.size	_force_create, .-_force_create
	.p2align 4
	.globl	_force_alloc
	.type	_force_alloc, @function
_force_alloc:
	endbr64
	lea	rax, -1[rsi]
	cmp	rax, 65519
	ja	.L24
	mov	rax, QWORD PTR 8[rdi]
	add	rax, 7
	and	rax, -8
	add	rsi, rax
	cmp	QWORD PTR 16[rdi], rsi
	jb	.L24
	mov	QWORD PTR 8[rdi], rsi
	add	rax, QWORD PTR [rdi]
	ret
	.p2align 4,,10
	.p2align 3
.L24:
	xor	eax, eax
	ret
	.size	_force_alloc, .-_force_alloc
	.p2align 4
	.globl	_force_reset
	.type	_force_reset, @function
_force_reset:
	endbr64
	add	QWORD PTR 24[rdi], 1
	mov	QWORD PTR 8[rdi], 0
	ret
	.size	_force_reset, .-_force_reset
	.p2align 4
	.globl	_force_destroy
	.type	_force_destroy, @function
_force_destroy:
	endbr64
	test	rdi, rdi
	je	.L26
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L26:
	ret
	.size	_force_destroy, .-_force_destroy
	.section	.rodata.cst16,"aM",@progbits,16
	.align 16
.LC0:
	.quad	0
	.quad	65520
	.ident	"GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
	.section	.note.GNU-stack,"",@progbits
	.section	.note.gnu.property,"a"
	.align 8
	.long	1f - 0f
	.long	4f - 1f
	.long	5
0:
	.string	"GNU"
1:
	.align 8
	.long	0xc0000002
	.long	3f - 2f
2:
	.long	0x3
3:
	.align 8
4:
